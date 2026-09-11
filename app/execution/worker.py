"""Run with python -m app.execution.worker. No worker runs inside FastAPI."""

import asyncio
import hashlib
import logging
import signal
import sys
import time
from pathlib import Path

import anyio

from app.config import Settings
from app.errors import DomainError
from app.execution.protocol import JobInput, JobOutput, JobProgress, atomic_json, read_manifest
from app.schemas.analysis import RunView
from app.schemas.common import ExecutionError
from app.storage.jobs import AssetRecord, JobRepository
from app.storage.memory import new_id
from app.workflows.registry import get_workflow

logger = logging.getLogger(__name__)


class LeaseLost(RuntimeError):
    pass


def prepare_directory(
    settings: Settings, asset: AssetRecord, run: RunView, failures: list[str]
) -> Path:
    source = asset.path.resolve()
    if not source.is_relative_to(settings.asset_root.resolve()):
        raise DomainError("ASSET_INTEGRITY_ERROR", "资产存储路径校验失败", 500)
    directory = settings.job_root.resolve() / run.run_id / str(run.attempt_id)
    directory.mkdir(parents=True, exist_ok=False)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as incoming, (directory / "geometry.input").open("xb") as staged:
        while chunk := incoming.read(65536):
            size += len(chunk)
            if size > settings.max_file_bytes:
                raise DomainError("ASSET_INTEGRITY_ERROR", "资产文件超过配置上限", 500)
            digest.update(chunk)
            staged.write(chunk)
    if size != asset.view.size_bytes or digest.hexdigest() != run.input_asset_sha256:
        raise DomainError("ASSET_INTEGRITY_ERROR", "资产内容与提交快照不一致", 500)
    command = JobInput(
        run_id=run.run_id,
        attempt_id=str(run.attempt_id),
        request=run.request,
        implementation_version=run.implementation_version,
        geometry_suffix=asset.view.suffix,
        geometry_sha256=run.input_asset_sha256,
        mock_delay_seconds=settings.mock_delay_seconds,
        mock_failures=failures,
    )
    atomic_json(directory / "input.json", command)
    return directory


def verify_identity(manifest: JobProgress, run: RunView):
    if manifest.run_id != run.run_id or manifest.attempt_id != run.attempt_id:
        raise ValueError("runner manifest does not belong to this attempt")
    definition = get_workflow(run.workflow_id, run.workflow_version)
    if [s.name for s in manifest.steps] != list(definition.steps):
        raise ValueError("runner step list differs from registered workflow")


def progress(directory: Path, run: RunView) -> JobProgress | None:
    try:
        result = JobProgress.model_validate(read_manifest(directory / "progress.json"))
    except FileNotFoundError:
        return None
    verify_identity(result, run)
    return result


def verified_output(directory: Path, run: RunView, exit_code: int) -> JobOutput:
    result = JobOutput.model_validate(read_manifest(directory / "result.json"))
    definition = get_workflow(run.workflow_id, run.workflow_version)
    verify_identity(result, run)
    if result.implementation_version != run.implementation_version:
        raise ValueError("runner implementation differs from pinned version")
    if result.status == "succeeded":
        if exit_code != 0 or any(s.status != "succeeded" for s in result.steps):
            raise ValueError("incomplete runner execution cannot succeed")
        payload = result.result
        if payload.completed_steps != list(definition.steps):
            raise ValueError("result does not match requested workflow")
        if len(payload.artifacts) != 1 or payload.artifacts[0].artifact_id != "summary":
            raise ValueError("expected one summary artifact")
        artifact = payload.artifacts[0]
        with (directory / "summary.json").open("rb") as source:
            data = source.read(1024 * 1024 + 1)
        if len(data) != artifact.size_bytes or hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise ValueError("result artifact checksum mismatch")
        summary = read_manifest(directory / "summary.json")
        definition.verify_result(run, payload, summary)
        artifact.download_url = f"/api/v1/analysis-runs/{run.run_id}/artifacts/summary"
    return result


async def stop_process(process: asyncio.subprocess.Process):
    """Mock has no child GUI/solver process. Real mode needs a verified process-tree strategy."""
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            process.kill()
            await process.wait()


class Worker:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository | None = None,
        *,
        failures: list[str] | None = None,
    ):
        if settings.provider != "mock":
            raise RuntimeError("真实 SDK / guierunner 尚未验证，禁止启动真实 Worker 或回退 Mock")
        self.settings = settings
        self.repository = repository or JobRepository(settings.database_path, settings.max_records)
        self.worker_id = new_id("worker")
        self.failures = failures or []
        self.stop = asyncio.Event()

    async def run_once(self) -> bool:
        claimed = await self.repository.claim(self.worker_id, self.settings.worker_lease_seconds)
        if claimed is None:
            return False
        owner, run = claimed
        process = None
        waiting = None
        output_log = error_log = None
        started = time.monotonic()
        try:
            definition = get_workflow(run.workflow_id, run.workflow_version)
            if definition.implementation_version != run.implementation_version:
                raise DomainError(
                    "WORKFLOW_VERSION_UNAVAILABLE", "Worker 不具备任务固定的实现版本", 503
                )
            if run.request.input.process_count > self.settings.max_process_num:
                raise DomainError("PROCESS_LIMIT_EXCEEDED", "执行节点进程数上限不足", 422)
            asset = await self.repository.asset(owner, run.request.input.geometry.geometry_asset_id)
            directory = await anyio.to_thread.run_sync(
                prepare_directory, self.settings, asset, run, self.failures
            )
            lease = await self.repository.heartbeat(
                run.run_id, run.attempt_id, self.settings.worker_lease_seconds
            )
            if lease is None:
                raise LeaseLost("lease expired before process launch")
            if lease.cancel_requested:
                await self.repository.finish(run.run_id, run.attempt_id, "cancelled")
                return True
            output_log = (directory / "stdout.log").open("wb")
            error_log = (directory / "stderr.log").open("wb")
            # Fixed module + argument array. No shell or client-controlled entry point.
            launch = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "app.execution.mock_entry",
                    "--job-dir",
                    str(directory),
                    cwd=Path(__file__).resolve().parents[2],
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=output_log,
                    stderr=error_log,
                )
            )
            try:
                process = await asyncio.shield(launch)
            except asyncio.CancelledError:
                process = await launch
                raise
            await anyio.to_thread.run_sync(
                atomic_json,
                directory / "process.json",
                {
                    "pid": process.pid,
                    "worker_id": self.worker_id,
                    "run_id": run.run_id,
                    "attempt_id": run.attempt_id,
                },
            )
            waiting = asyncio.create_task(process.wait())
            while True:
                current = await anyio.to_thread.run_sync(progress, directory, run)
                lease = await self.repository.heartbeat(
                    run.run_id,
                    run.attempt_id,
                    self.settings.worker_lease_seconds,
                    current.steps if current else None,
                )
                if lease is None:
                    raise LeaseLost("execution lease lost; old worker cannot publish results")
                if waiting.done():
                    break
                if (
                    lease.cancel_requested
                    or self.stop.is_set()
                    or time.monotonic() - started >= self.settings.run_timeout_seconds
                ):
                    await stop_process(process)
                    if lease.cancel_requested:
                        status, code, detail = "cancelled", "CANCELLED", "Mock 执行进程已确认停止"
                    elif self.stop.is_set():
                        status, code, detail = (
                            "unknown",
                            "WORKER_STOPPED",
                            "Worker 停止，保留执行记录等待核对",
                        )
                    else:
                        status, code, detail = (
                            "timed_out",
                            "RUN_TIMEOUT",
                            "Mock 执行超时，进程已确认停止",
                        )
                    await self.repository.finish(
                        run.run_id,
                        run.attempt_id,
                        status,
                        error=ExecutionError(code=code, detail=detail),
                    )
                    return True
                try:
                    await asyncio.wait_for(
                        asyncio.shield(waiting),
                        timeout=min(
                            self.settings.worker_poll_seconds,
                            self.settings.worker_lease_seconds / 3,
                        ),
                    )
                except TimeoutError:
                    pass
            result = await anyio.to_thread.run_sync(
                verified_output, directory, run, process.returncode
            )
            await self.repository.finish(
                run.run_id,
                run.attempt_id,
                result.status,
                steps=result.steps,
                result=result.result,
                error=result.error,
                runtime_versions=result.runtime_versions,
            )
        except LeaseLost:
            logger.warning("run_id=%s lease lost; result discarded", run.run_id)
        except asyncio.CancelledError:
            if process is not None:
                await stop_process(process)
            await self.repository.finish(
                run.run_id,
                run.attempt_id,
                "unknown",
                error=ExecutionError(
                    code="WORKER_INTERRUPTED", detail="Worker 已中断，不自动重放任务"
                ),
            )
            raise
        except Exception as exc:
            if process is not None:
                await stop_process(process)
            logger.exception("run_id=%s execution failed", run.run_id)
            error = (
                ExecutionError(code=exc.code, detail=exc.detail)
                if isinstance(exc, DomainError)
                else ExecutionError(
                    code="RUNNER_EXECUTION_ERROR",
                    detail="执行器启动、协议或结果校验失败，请按 run_id 查看日志",
                )
            )
            await self.repository.finish(run.run_id, run.attempt_id, "failed", error=error)
        finally:
            if process is not None:
                await stop_process(process)
            if waiting:
                await waiting
            if output_log:
                output_log.close()
            if error_log:
                error_log.close()
        return True

    async def serve(self):
        await self.repository.initialize()
        logger.warning("Mock Worker %s: one database execution slot; no real CAE", self.worker_id)
        while not self.stop.is_set():
            if not await self.run_once():
                try:
                    await asyncio.wait_for(
                        self.stop.wait(), timeout=self.settings.worker_poll_seconds
                    )
                except TimeoutError:
                    pass


async def main():
    worker = Worker(Settings())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.stop.set)
        except NotImplementedError:
            pass
    await worker.serve()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
