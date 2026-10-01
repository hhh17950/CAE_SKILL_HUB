from __future__ import annotations

import asyncio
import base64
import binascii
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from core.config import Settings
from services import paths
from storage.repository import RunRepository

SERVER_NAME = "茉莉平台CAE仿真"
# Host values the MCP transport always accepts, matching the SDK defaults for local use.
LOCAL_HOSTS = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "[::1]", "[::1]:*"]


def transport_security(settings: Settings) -> TransportSecuritySettings:
    """Allow the deployment host to reach /mcp; the SDK rejects unknown Host headers (421).

    CAE_PUBLIC_BASE_URL 的 host 会自动加入白名单；需要额外 host 时用逗号分隔的
    CAE_MCP_ALLOWED_HOSTS 补充，值为 ``*`` 表示关闭该校验（内网自有网络下才建议）。
    """
    configured = [item.strip() for item in settings.mcp_allowed_hosts.split(",") if item.strip()]
    if "*" in configured:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    allowed = list(LOCAL_HOSTS)
    public = urlparse(settings.public_base_url)
    if public.netloc:
        allowed.append(public.netloc)
    if public.hostname:
        allowed.append(f"{public.hostname}:*")
    allowed.extend(configured)
    return TransportSecuritySettings(allowed_hosts=allowed)


def build_mcp_server(settings: Settings) -> FastMCP:
    """Build a fresh MCP server; its session manager may only be run once per instance."""
    server = FastMCP(SERVER_NAME, transport_security=transport_security(settings))
    for tool in (submit_modal_run, get_run_status, get_run_result, get_run_log):
        server.tool()(tool)
    return server


def _repo(settings: Settings) -> RunRepository:
    return RunRepository(settings.database_path)


async def submit_modal_run(
    model_b64: str,
    model_filename: str,
    number_of_roots: int = 10,
    young_modulus: float = 2.0e11,
    poisson_ratio: float = 0.3,
    density: int = 7850,
) -> dict[str, Any]:
    """提交一个"茉莉平台 - 结构模态案例仿真流程"任务。

    本服务（茉莉平台CAE仿真）只处理茉莉平台内的仿真流程，与服务器上其他开发者提供的
    独立仿真服务（如ansys等）无关。判断依据：计算与"模态 / 固有频率 / 振型 / 
    特征频率"相关的结构分析时，使用本工具。

    参数:
        model_b64 (str): 几何模型文件的二进制内容，Base64 编码（不接受文件路径，必填）
        model_filename (str): 模型文件名（如 modal.stp），仅用于服务端保存命名（必填）
        number_of_roots (int): 模态阶数，>= 1. Defaults to 10.
        young_modulus (float): 杨氏模量. Defaults to 2.0e11.
        poisson_ratio (float): 泊松比，取值 (-1, 0.5). Defaults to 0.3.
        density (int): 密度. Defaults to 7850.

    当用户未提供物理参数时使用上述默认值（钢材料）；不同材料请传入对应值。

    返回:
        包含 ``run_id`` 与 ``status_url`` 的字典；用 get_run_status 轮询任务。
    """
    # 参数校验（与 HTTP 路由的约束保持一致）
    if young_modulus <= 0:
        raise ValueError("young_modulus 必须 > 0")
    if not (-1 < poisson_ratio < 0.5):
        raise ValueError("poisson_ratio 必须满足 -1 < ratio < 0.5")
    if density <= 0:
        raise ValueError("density 必须 > 0")
    if number_of_roots < 1:
        raise ValueError("number_of_roots 必须 >= 1")

    settings = Settings()
    # 解码 Base64（服务端控制路径，杜绝外部路径）
    try:
        content = base64.b64decode(model_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("model_b64 不是合法的 Base64内容") from exc
    if len(content) == 0:
        raise ValueError("模型文件为空")
    if len(content) > settings.max_model_bytes:
        raise ValueError(
            f"模型文件超过服务限制 {settings.max_model_bytes} 字节，请改用 REST multipart 上传"
        )

    filename = Path(model_filename or "modal.stp").name
    run_id = f"run_{uuid4().hex}"
    submit_dir = paths.create_task_dir(settings, run_id)
    paths.model_path(submit_dir, filename).write_bytes(content)

    parameters = {
        "young_modulus": young_modulus,
        "poisson_ratio": poisson_ratio,
        "density": density,
        "number_of_roots": number_of_roots,
        "model_filename": filename,
    }
    repo = _repo(settings)
    try:
        row = await repo.submit(run_id, parameters)
    finally:
        await repo.close()
    return {
        "run_id": row["run_id"],
        "status": row["status"],
        "status_url": f"/api/v1/guie-runs/{run_id}",
        "message": "任务已排队，Worker 将异步执行；请用 get_run_status 轮询",
    }


async def get_run_status(run_id: str) -> dict[str, Any]:
    """查询"茉莉平台 - 结构模态案例仿真流程"任务的状态。

    返回 run_id、status、created_at、started_at、finished_at、exit_code、error。
    用于轮询 submit_modal_run（或其他 submit_*）提交的任务，直到 status 变为 succeeded。
    """
    repo = _repo(Settings())
    try:
        row = await repo.get(run_id)
    finally:
        await repo.close()
    if row is None:
        raise ValueError("run_id 不存在")
    return {
        "run_id": row["run_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "exit_code": row.get("exit_code"),
        "error": row.get("error"),
    }


async def get_run_result(run_id: str) -> dict[str, Any]:
    """读取已完成的"茉莉平台 - 结构模态案例仿真流程"任务的结果。

    仅当任务 succeeded 且生成了 cloud_info.json 时返回结果内容。cloud_info 是按模态阶数编号的
    字典，每张云图对应一个模态；每条含 cloud_file_name（服务器本地路径，智能体不可访问）与
    frequency（该模态频率）。本工具还会把每条 cloud_file_name 转成一个可下载的公开 URL
    放进该条目的``image_url``字段；``{public_base_url}/api/v1/guie-runs/{run_id}/cloud/{basename}``。
    智能体只需用任意 HTTP 客户端 GET 该 URL 即可拿到对应的 PNG。
    """
    settings = Settings()
    repo = _repo(settings)
    try:
        row = await repo.get(run_id)
    finally:
        await repo.close()
    if row is None:
        raise ValueError("run_id 不存在")
    if row["status"] != "succeeded":
        raise ValueError(f"任务状态为 {row['status']}，尚未成功，请先 get_run_status 轮询")

    path = paths.cloud_info(paths.task_dir(settings, run_id))
    if not path.is_file():
        raise ValueError("脚本未生成 cloud_info.json")
    content = await asyncio.to_thread(path.read_text, encoding="utf-8")
    try:
        cloud_info = json.loads(content)
    except ValueError as exc:
        raise ValueError("结果文件不是有效 JSON") from exc

    if isinstance(cloud_info, dict):
        base = settings.public_base_url.rstrip("/")
        for entry in cloud_info.values():
            if isinstance(entry, dict) and entry.get("cloud_file_name"):
                filename = Path(entry["cloud_file_name"]).name
                entry["image_url"] = f"{base}/api/v1/guie-runs/{run_id}/cloud/{filename}"

    return {"run_id": run_id, "exit_code": row["exit_code"], "cloud_info": cloud_info}


async def get_run_log(run_id: str, kind: str) -> str:
    """读取"茉莉平台 - 结构模态案例仿真流程"任务日志内容，kind 可取 stdout / stderr / jusmar。

    当任务 failed 或 timed_out 时，用它读取日志诊断失败原因。
    """
    if kind not in {"stdout", "stderr", "jusmar"}:
        raise ValueError("kind 必须为 stdout / stderr / jusmar")
    settings = Settings()
    repo = _repo(settings)
    try:
        row = await repo.get(run_id)
    finally:
        await repo.close()
    if row is None:
        raise ValueError("run_id 不存在")
    run_dir = paths.task_dir(settings, run_id)
    if kind == "jusmar":
        path = paths.jusmar_log(run_dir)
    elif kind == "stderr":
        path = paths.stderr_log(run_dir)
    else:
        path = paths.stdout_log(run_dir)
    if not path.is_file():
        raise ValueError("该任务无此日志文件")
    return await asyncio.to_thread(path.read_text, encoding="utf-8")
