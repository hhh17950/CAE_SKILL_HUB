import asyncio
import json
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, Response

from api.errors import DomainError, not_found
from api.schemas import GuieRunView
from services import paths
from storage.repository import RunRepository

router = APIRouter(prefix="/guie-runs", tags=["guie-runs"])


def store(request: Request) -> RunRepository:
    return request.app.state.guie_store


async def owned_run(request: Request, run_id: str) -> GuieRunView:
    return run_view(await owned_record(request, run_id))


async def owned_record(request: Request, run_id: str) -> dict:
    found = await store(request).get(run_id)
    if found is None:
        raise not_found("任务")
    return found


def run_view(row: dict) -> GuieRunView:
    base = f"/api/v1/guie-runs/{row['run_id']}"
    return GuieRunView(
        **{
            name: row[name]
            for name in (
                "run_id",
                "status",
                "created_at",
                "started_at",
                "finished_at",
                "exit_code",
                "error",
            )
        },
        status_url=base,
        result_url=f"{base}/results",
        log_urls={kind: f"{base}/logs/{kind}" for kind in ("stdout", "stderr", "jusmar")},
    )


@router.post(
    "/modal", response_model=GuieRunView, status_code=202, operation_id="submit_modal_guie_run"
)
async def submit_modal_guie_run(
    request: Request,
    response: Response,
    model_file: UploadFile = File(description="上传的几何模型文件"),
    young_modulus: Annotated[float, Form(gt=0)] = 2.0e11,
    poisson_ratio: Annotated[float, Form(gt=-1, lt=0.5)] = 0.3,
    density: Annotated[int, Form(gt=0)] = 7850,
    number_of_roots: Annotated[int, Form(ge=1)] = 10,
):
    settings = request.app.state.settings
    parameters = {
        "young_modulus": young_modulus,
        "poisson_ratio": poisson_ratio,
        "density": density,
        "number_of_roots": number_of_roots,
    }
    filename = Path(model_file.filename or "modal.stp").name
    run_id = f"run_{uuid4().hex}"
    submit_dir = paths.create_task_dir(settings, run_id)
    model = paths.model_path(submit_dir, filename)
    written = 0
    # Binary mode: the uploaded geometry file must reach the worker byte-for-byte, and a text
    # handle would raise TypeError on bytes chunks (and rewrite line endings on Windows).
    with model.open("wb") as target:
        while chunk := await model_file.read(1024 * 1024):
            written += len(chunk)
            if written > settings.max_model_bytes:
                raise DomainError("MODEL_TOO_LARGE", "上传的几何模型文件超过服务限制", 413)
            target.write(chunk)
    if written == 0:
        raise DomainError("MODEL_EMPTY", "上传的几何模型为空", 422)
    parameters["model_filename"] = filename
    run = run_view(await store(request).submit(run_id, parameters))
    response.headers["Location"] = run.status_url
    return run


@router.get("/{run_id}", response_model=GuieRunView, operation_id="get_guie_run")
async def get_guie_run(run_id: str, request: Request):
    return await owned_run(request, run_id)


@router.get("/{run_id}/cloud/{filename}", operation_id="get_guie_cloud_image")
async def get_guie_cloud_image(run_id: str, filename: str, request: Request):
    """通过 cloud_info.json 把文件名映射到真实路径，然后返回该图片文件。

    只允许读取当前任务 cloud_info.json 中真实声明的 cloud_file_name，
    filename 必须匹配 basename，杜绝路径遍历。智能体拿到 URL 后自行下载。
    """
    submit_dir = paths.task_dir(request.app.state.settings, run_id)
    info_path = paths.cloud_info(submit_dir)
    if not info_path.is_file():
        raise not_found("该任务的云图信息文件")
    try:
        content = await asyncio.to_thread(info_path.read_text, encoding="utf-8")
        cloud_info = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise DomainError("RESULT_INVALID", "云图信息文件不是有效 JSON", 500) from exc
    if not isinstance(cloud_info, dict):
        raise not_found("云图文件")
    for entry in cloud_info.values():
        if isinstance(entry, dict) and Path(entry["cloud_file_name"]).name == filename:
            target = Path(entry["cloud_file_name"])
            if not target.is_file():
                raise not_found("云图文件不存在")
            return FileResponse(target, media_type="image/png")
    raise not_found("云图文件")


@router.get("/{run_id}/results", operation_id="get_guie_results")
async def get_guie_results(run_id: str, request: Request):
    record = await owned_record(request, run_id)
    run = run_view(record)
    if run.status != "succeeded":
        raise DomainError("RESULT_NOT_READY", "任务尚未正常退出", 409)
    submit_dir = paths.task_dir(request.app.state.settings, run_id)
    path = paths.cloud_info(submit_dir)
    if not path.is_file():
        raise DomainError("RESULT_NOT_FOUND", "脚本未生成云图信息文件", 404)
    try:
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        cloud_info = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise DomainError("RESULT_INVALID", "脚本生成的结果文件不是有效 JSON", 500) from exc
    cloud_dir = None
    if isinstance(cloud_info, dict) and cloud_info:
        first = next(iter(cloud_info.values()))
        cloud_dir = str(Path(first["cloud_file_name"]).parent)
    return {
        "run_id": run_id,
        "exit_code": run.exit_code,
        "cloud_dir": cloud_dir,
        "cloud_info": cloud_info,
    }


@router.get("/{run_id}/logs/{kind}", operation_id="get_guie_log")
async def get_guie_log(kind: Literal["stdout", "stderr", "jusmar"], run_id: str, request: Request):
    submit_dir = paths.task_dir(request.app.state.settings, run_id)
    path = paths.stdout_log(submit_dir) if kind == "stdout" else paths.stderr_log(submit_dir)
    if kind == "jusmar":
        path = paths.jusmar_log(submit_dir)
    if not path.is_file():
        raise not_found("任务日志")
    return FileResponse(path, media_type="text/plain; charset=utf-8")
