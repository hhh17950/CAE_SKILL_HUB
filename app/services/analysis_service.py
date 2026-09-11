import hashlib
import json
from pathlib import Path
from typing import Protocol

import anyio

from app.config import Settings
from app.errors import DomainError, not_found
from app.schemas.analysis import (
    AnalysisRequest,
    AssetView,
    RunView,
    StepView,
    ValidationCheck,
    ValidationReport,
)
from app.storage.jobs import AssetRecord, JobRepository
from app.storage.memory import new_id, now
from app.workflows.registry import get_workflow

ALLOWED_SUFFIXES = {".stp", ".step", ".igs", ".iges", ".x_t"}


class UploadSource(Protocol):
    filename: str | None

    async def read(self, size: int) -> bytes: ...
    async def close(self) -> None: ...


class AnalysisService:
    def __init__(self, settings: Settings, repository: JobRepository):
        self.settings = settings
        self.repository = repository

    async def upload(self, owner: str, upload: UploadSource) -> AssetView:
        name = upload.filename or ""
        if (
            not name
            or len(name) > 200
            or "/" in name
            or "\\" in name
            or any(ord(c) < 32 for c in name)
        ):
            raise DomainError("INVALID_FILE_NAME", "文件名必须为不含目录的普通文件名", 422)
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise DomainError("UNSUPPORTED_FILE_FORMAT", "当前不支持该文件后缀", 422)
        asset_id = new_id("asset")
        root = self.settings.asset_root.resolve()
        target = root / (asset_id + suffix)
        temporary = root / (asset_id + ".part")
        await anyio.Path(root).mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        metadata_attempted = False
        try:
            async with await anyio.open_file(temporary, "xb") as output:
                while chunk := await upload.read(65536):
                    size += len(chunk)
                    if size > self.settings.max_file_bytes:
                        raise DomainError("FILE_TOO_LARGE", "文件超过配置大小上限", 413)
                    digest.update(chunk)
                    await output.write(chunk)
                await output.flush()
            if not size:
                raise DomainError("EMPTY_FILE", "几何文件不能为空", 422)
            await anyio.Path(temporary).replace(target)
            view = AssetView(
                asset_id=asset_id,
                file_name=name,
                suffix=suffix,
                size_bytes=size,
                sha256=digest.hexdigest(),
                created_at=now(),
            )
            metadata_attempted = True
            await self.repository.add_asset(AssetRecord(owner, target, view))
            return view
        except BaseException:
            # These two paths are newly generated for this upload only.
            with anyio.CancelScope(shield=True):
                await anyio.Path(temporary).unlink(missing_ok=True)
                # A cancelled commit can have an uncertain outcome. Preserve its file.
                if not metadata_attempted:
                    await anyio.Path(target).unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

    async def validate(self, owner: str, request: AnalysisRequest) -> ValidationReport:
        get_workflow(request.workflow_id, request.workflow_version)
        await self.repository.asset(owner, request.input.geometry.geometry_asset_id)
        enough = request.input.process_count <= self.settings.max_process_num
        return ValidationReport(
            valid=enough,
            normalized_request=request,
            checks=[
                ValidationCheck(
                    name="schema",
                    status="passed",
                    code="SCHEMA_VALID",
                    detail="字段与支持的业务组合符合当前 Schema",
                ),
                ValidationCheck(
                    name="asset",
                    status="passed",
                    code="ASSET_REGISTERED",
                    detail="输入资产已登记且属于当前调用方；尚未解析 CAD",
                ),
                ValidationCheck(
                    name="process_count",
                    status="passed" if enough else "failed",
                    code="PROCESS_LIMIT_OK" if enough else "PROCESS_LIMIT_EXCEEDED",
                    detail=f"进程数上限为 {self.settings.max_process_num}",
                ),
                ValidationCheck(
                    name="worker_environment",
                    status="deferred",
                    code="CHECK_AT_EXECUTION",
                    detail="文件可达性与内容校验值在 Worker 执行前核对",
                ),
                ValidationCheck(
                    name="cae_model",
                    status="deferred",
                    code="REAL_CAE_NOT_AVAILABLE",
                    detail="Mock 不检查几何有效性、网格质量或物理正确性",
                ),
            ],
        )

    async def submit(
        self, owner: str, key: str, request: AnalysisRequest, raw_request: dict
    ) -> RunView:
        canonical = json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        existing = await self.repository.replay(owner, key, fingerprint)
        if existing:
            return existing
        report = await self.validate(owner, request)
        if not report.valid:
            raise DomainError("PROCESS_LIMIT_EXCEEDED", "进程数超过服务配置上限", 422)
        asset = await self.repository.asset(owner, request.input.geometry.geometry_asset_id)
        definition = get_workflow(request.workflow_id, request.workflow_version)
        view = RunView(
            run_id=new_id("run"),
            workflow_id=request.workflow_id,
            workflow_version=request.workflow_version,
            implementation_version=definition.implementation_version,
            status="queued",
            created_at=now(),
            updated_at=now(),
            request=request,
            input_asset_sha256=asset.view.sha256,
            steps=[StepView(name=name, status="pending") for name in definition.steps],
        )
        return await self.repository.submit(
            owner, key, fingerprint, json.dumps(raw_request, ensure_ascii=False), view
        )

    async def artifact(self, owner: str, run_id: str, artifact_id: str) -> bytes:
        run = await self.repository.run(owner, run_id)
        if run.status != "succeeded" or run.result is None:
            raise DomainError("RESULT_NOT_READY", "任务尚无完整可交付结果", 409)
        artifact = next((a for a in run.result.artifacts if a.artifact_id == artifact_id), None)
        if artifact is None or artifact_id != "summary":
            raise not_found("结果文件")
        root = self.settings.job_root.resolve()
        path = (root / run_id / str(run.attempt_id) / "summary.json").resolve()
        if not path.is_relative_to(root):
            raise DomainError("ARTIFACT_INTEGRITY_ERROR", "结果路径校验失败", 500)
        try:
            # The currently supported artifact is a bounded JSON summary, not a large CAE file.
            async with await anyio.open_file(path, "rb") as source:
                data = await source.read(1024 * 1024 + 1)
        except OSError as exc:
            raise DomainError("ARTIFACT_UNAVAILABLE", "结果文件不可用", 503) from exc
        if len(data) != artifact.size_bytes or hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise DomainError("ARTIFACT_INTEGRITY_ERROR", "结果文件完整性校验失败", 500)
        return data
