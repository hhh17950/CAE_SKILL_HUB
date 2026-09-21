"""HTTP endpoints for one fixed guie2 script flow."""

import asyncio
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from api.dependencies import Owner
from api.errors import DomainError, not_found
from api.schemas import GuieRunRequest, GuieRunView
from services.paths import existing_project_dir, new_output_file
from storage.repository import RunRepository

router = APIRouter(prefix="/guie-runs", tags=["guie-runs"])


def store(request: Request) -> RunRepository:
    return request.app.state.guie_store


async def owned_run(request: Request, owner: str, run_id: str) -> GuieRunView:
    return run_view(await owned_record(request, owner, run_id))


async def owned_record(request: Request, owner: str, run_id: str) -> dict:
    found = await store(request).get(owner, run_id)
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


def checked_model_path(request: Request, model_path: str) -> Path:
    settings = request.app.state.settings
    root = settings.model_root
    if root is None:
        raise DomainError("MODEL_PATH_DISABLED", "服务未配置可访问的模型根目录", 422)
    path = Path(model_path)
    if not path.is_absolute():
        raise DomainError("MODEL_PATH_INVALID", "模型路径必须为服务端绝对路径", 422)
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise DomainError("MODEL_PATH_INVALID", "模型文件不存在或超出允许目录", 422)
    return resolved


@router.post("", response_model=GuieRunView, status_code=202, operation_id="submit_guie_run")
async def submit_guie_run(body: GuieRunRequest, request: Request, owner: Owner, response: Response):
    model = checked_model_path(request, body.model_path)
    settings = request.app.state.settings
    parameters = body.model_dump()
    parameters["model_path"] = str(model)
    try:
        parameters["project_dir"] = str(existing_project_dir(body.project_dir, settings))
        for name in ("jusmar_log_path", "cloud_info_path"):
            if value := getattr(body, name):
                parameters[name] = str(new_output_file(value, settings, name))
        if (
            parameters["jusmar_log_path"] == parameters["cloud_info_path"]
            and parameters["jusmar_log_path"] is not None
        ):
            raise ValueError("求解器日志和云图信息不能使用同一个文件")
    except ValueError as exc:
        raise DomainError("PATH_INVALID", str(exc), 422) from exc
    try:
        run = run_view(await store(request).submit(owner, parameters))
    except ValueError as exc:
        raise DomainError("OUTPUT_PATH_IN_USE", str(exc), 422) from exc
    response.headers["Location"] = run.status_url
    return run


@router.get("/{run_id}", response_model=GuieRunView, operation_id="get_guie_run")
async def get_guie_run(run_id: str, request: Request, owner: Owner):
    return await owned_run(request, owner, run_id)


@router.get("/{run_id}/results", operation_id="get_guie_results")
async def get_guie_results(run_id: str, request: Request, owner: Owner):
    record = await owned_record(request, owner, run_id)
    run = run_view(record)
    if run.status != "succeeded":
        raise DomainError("RESULT_NOT_READY", "任务尚未正常退出", 409)
    parameters = json.loads(record["request_json"])
    path = Path(
        parameters["cloud_info_path"]
        or request.app.state.settings.run_root.resolve() / run_id / "cloud_info.json"
    )
    if not path.is_file():
        raise DomainError("RESULT_NOT_FOUND", "脚本未生成云图信息文件", 404)
    try:
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        cloud_info = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise DomainError("RESULT_INVALID", "脚本生成的结果文件不是有效 JSON", 500) from exc
    return {"run_id": run_id, "exit_code": run.exit_code, "cloud_info": cloud_info}


@router.get("/{run_id}/logs/{kind}", operation_id="get_guie_log")
async def get_guie_log(
    kind: Literal["stdout", "stderr", "jusmar"], run_id: str, request: Request, owner: Owner
):
    record = await owned_record(request, owner, run_id)
    path = request.app.state.settings.task_log_root.resolve() / run_id / f"{kind}.log"
    if kind == "jusmar":
        parameters = json.loads(record["request_json"])
        if parameters["jusmar_log_path"]:
            path = Path(parameters["jusmar_log_path"])
    if not path.is_file():
        raise not_found("任务日志")
    return FileResponse(path, media_type="text/plain; charset=utf-8")
