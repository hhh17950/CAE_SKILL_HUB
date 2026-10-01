from __future__ import annotations

import asyncio
import base64
import binascii
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from core.config import Settings
from services import paths
from storage.repository import RunRepository

SERVER_NAME = "茉莉平台CAE仿真"
SERVER_TITLE = "茉莉平台 - 结构模态案例仿真流程"
SERVER_DESCRIPTION = "向茉莉平台提交几何模型的结构模态仿真任务，并查询状态、日志与云图结果。"
# 与 initialize 一起返回给智能体，是唯一一处"全局用法说明"。工具描述会被逐条展开，这段说明负责
# 交代流程顺序、必须由用户提供的输入，以及失败时该怎么走；不写在这里，智能体只能从参数 schema
# 猜出"要传一个 Base64 模型"，却不知道要先向用户要文件、也不知道结果里会给云图 URL。
SERVER_INSTRUCTIONS = """\
本服务把几何模型的“结构模态案例仿真流程”提交到茉莉平台执行，并回传每个模态的固有频率与振型云图。
它只处理茉莉平台内的这一条流程；服务器上其他开发者提供的仿真服务（如 ansys 等）与本服务无关。

调用顺序（四个工具构成一条链，请按序使用）：
1. submit_modal_run —— 提交任务，得到 run_id。
2. get_run_status(run_id) —— 轮询到 status 变为 succeeded / failed / timed_out。
3. succeeded 后调用 get_run_result(run_id) —— 拿到每个模态的 frequency 与云图 image_url。
4. failed / timed_out 时调用 get_run_log(run_id, kind) —— 读日志定位原因。

提交任务必须由用户提供几何模型文件：工具接收的是文件字节的 Base64 内容（model_b64）与文件名
（model_filename），不接受文件路径。用户没有给出模型文件时，先向用户索取（请其上传文件或给出可
读取的路径），不要自行编造几何体、也不要用示例文件冒充用户模型；拿到文件后自行读取并 Base64 编码。
杨氏模量 / 泊松比 / 密度 / 模态阶数都可省略，省略即按钢的默认值计算（见 submit_modal_run），
此时应在回复中说明“使用的是默认参数”，不要把它说成用户提供的值。

“返回云图”由 get_run_result 完成：结果里每条模态都带一个 image_url，用任意 HTTP 客户端 GET 该
URL 即可得到 PNG 云图；不要尝试读取服务器本地路径 cloud_file_name。
"""

# 工具注解：submit 会创建任务（非只读），其余三个只读取已有记录与文件。
SUBMIT_ANNOTATIONS = ToolAnnotations(read_only_hint=False)
READ_ANNOTATIONS = ToolAnnotations(read_only_hint=True)
# The service is deployed inside one intranet with no authentication of its own (access control
# belongs to the deployment layer: reverse proxy, firewall, directory permissions). The SDK's
# Host check would only decide which addresses may reach /mcp and answers 421 otherwise, which
# breaks every caller that uses an address other than CAE_PUBLIC_BASE_URL. It also does not act
# as access control: any host on the network can already POST to this endpoint directly.
DISABLED_TRANSPORT_SECURITY = TransportSecuritySettings(enable_dns_rebinding_protection=False)


def build_mcp_server(settings: Settings) -> MCPServer:
    """Build a fresh MCP server; its session manager may only be run once per instance."""
    server = MCPServer(
        SERVER_NAME,
        title=SERVER_TITLE,
        description=SERVER_DESCRIPTION,
        instructions=SERVER_INSTRUCTIONS,
    )
    server.tool(title="提交结构模态仿真任务", annotations=SUBMIT_ANNOTATIONS)(submit_modal_run)
    server.tool(title="查询任务状态", annotations=READ_ANNOTATIONS)(get_run_status)
    server.tool(title="读取云图结果", annotations=READ_ANNOTATIONS)(get_run_result)
    server.tool(title="读取任务日志", annotations=READ_ANNOTATIONS)(get_run_log)
    return server


def http_app(server: MCPServer, settings: Settings):
    """The Streamable HTTP ASGI app for this server; it also creates its session manager.

    mcp 2.x takes the transport settings here instead of in the constructor; the SDK default
    request body cap (4 MiB) is raised to CAE_MAX_REQUEST_BYTES, because a geometry model is
    sent as Base64 through this transport and would otherwise be rejected before our own
    CAE_MAX_MODEL_BYTES check runs.
    """
    return server.streamable_http_app(
        transport_security=DISABLED_TRANSPORT_SECURITY,
        max_request_body_size=settings.max_request_bytes,
    )


def _repo(settings: Settings) -> RunRepository:
    """Repository for a tool's own ``Settings()``: the tools read the process environment, the same
    ``.env`` the API and the Worker are started with."""
    return RunRepository(settings.database_path)


async def submit_modal_run(
    model_b64: str,
    model_filename: str,
    number_of_roots: int = 10,
    young_modulus: float = 2.0e11,
    poisson_ratio: float = 0.3,
    density: int = 7850,
) -> dict[str, Any]:
    """提交一个"茉莉平台 - 结构模态案例仿真流程"任务（本服务的入口工具）。

    适用场景：用户要求对某个几何模型做模态分析、求固有频率/振型，或要求"用茉莉平台帮我仿真并返回云图"。
    本服务只处理茉莉平台内的仿真流程，与服务器上其他开发者提供的独立仿真服务（如ansys等）无关。

    调用前必须拿到用户的几何模型文件（``model_b64`` 与 ``model_filename`` 均为必填）：
        - 用户没有提供模型文件时，先向用户索取（请其上传文件，或给出你能读取的路径）；
          不要自行编造几何体，也不要用示例文件冒充用户的模型。
        - 本工具只接收 Base64 内容、不接受文件路径；请自行读取文件字节并 Base64 编码后传入。
        - 文件名仅用于服务端保存与日志展示，用用户的原文件名即可。

    物理参数可以省略：省略即按下述默认值（钢）计算，此时应在回复中说明"使用的是默认参数"。

    参数:
        model_b64 (str): 几何模型文件字节的 Base64 编码（必填）
        model_filename (str): 模型文件名，如 modal.stp（必填）
        number_of_roots (int): 模态阶数，>= 1。默认 10。
        young_modulus (float): 杨氏模量，> 0。默认 2.0e11。
        poisson_ratio (float): 泊松比，取值 (-1, 0.5)。默认 0.3。
        density (int): 密度，> 0。默认 7850。

    返回:
        含 ``run_id``、``status``、``status_url`` 的字典。任务由 Worker 异步执行：
        用 get_run_status(run_id) 轮询，succeeded 后用 get_run_result(run_id) 取云图。
    """
    # 参数校验（与 HTTP 路由的约束保持一致）
    # 统一抛 ToolError：SDK 会把 ToolError 的文案原样带给调用方（"Error executing tool X: <文案>"），
    # 而普通异常（如 ValueError）只回一句 "Error executing tool X"，智能体看不到失败原因、无法自纠。
    if young_modulus <= 0:
        raise ToolError("young_modulus 必须 > 0")
    if not (-1 < poisson_ratio < 0.5):
        raise ToolError("poisson_ratio 必须满足 -1 < ratio < 0.5")
    if density <= 0:
        raise ToolError("density 必须 > 0")
    if number_of_roots < 1:
        raise ToolError("number_of_roots 必须 >= 1")

    settings = Settings()
    # 解码 Base64（服务端控制路径，杜绝外部路径）
    try:
        content = base64.b64decode(model_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("model_b64 不是合法的 Base64内容") from exc
    if len(content) == 0:
        raise ToolError("模型文件为空")
    if len(content) > settings.max_model_bytes:
        raise ToolError(
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

    轮询 submit_modal_run 返回的 run_id，按 status 决定下一步：
        - queued / running：任务进行中，等待数秒后再次调用本工具。
        - succeeded：调用 get_run_result 取每个模态的频率与云图 URL。
        - failed / timed_out：调用 get_run_log 读日志定位失败原因。
        - unknown：Worker 曾异常中断，该任务不会再变化。

    返回:
        run_id、status、created_at、started_at、finished_at、exit_code、error。
    """
    repo = _repo(Settings())
    try:
        row = await repo.get(run_id)
    finally:
        await repo.close()
    if row is None:
        raise ToolError("run_id 不存在")
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
    """读取已完成的"茉莉平台 - 结构模态案例仿真流程"任务的结果（"返回云图"用的就是它）。

    仅当任务 status 为 succeeded 时可用；否则会返回"任务状态为 xxx"的错误，请先用 get_run_status 轮询。

    返回:
        含 ``run_id``、``exit_code``、``cloud_info`` 的字典。cloud_info 以模态阶数为键，每个模态一条：
            - frequency：该阶固有频率；
            - cloud_file_name：服务器本地路径，智能体不可访问，不要尝试读取；
            - image_url：该模态振型云图（PNG）的公开下载地址，形如
              ``{public_base_url}/api/v1/guie-runs/{run_id}/cloud/{文件名}``。

        把 image_url 用任意 HTTP 客户端 GET 一次即可得到 PNG 图片；交给用户即可展示云图。
    """
    settings = Settings()
    repo = _repo(settings)
    try:
        row = await repo.get(run_id)
    finally:
        await repo.close()
    if row is None:
        raise ToolError("run_id 不存在")
    if row["status"] != "succeeded":
        raise ToolError(f"任务状态为 {row['status']}，尚未成功，请先 get_run_status 轮询")

    path = paths.cloud_info(paths.task_dir(settings, run_id))
    if not path.is_file():
        raise ToolError("脚本未生成 cloud_info.json")
    content = await asyncio.to_thread(path.read_text, encoding="utf-8")
    try:
        cloud_info = json.loads(content)
    except ValueError as exc:
        raise ToolError("结果文件不是有效 JSON") from exc

    if isinstance(cloud_info, dict):
        base = settings.public_base_url.rstrip("/")
        for entry in cloud_info.values():
            if isinstance(entry, dict) and entry.get("cloud_file_name"):
                filename = Path(entry["cloud_file_name"]).name
                entry["image_url"] = f"{base}/api/v1/guie-runs/{run_id}/cloud/{filename}"

    return {"run_id": run_id, "exit_code": row["exit_code"], "cloud_info": cloud_info}


async def get_run_log(run_id: str, kind: str) -> str:
    """读取"茉莉平台 - 结构模态案例仿真流程"任务的日志，kind 取 stdout / stderr / jusmar。

    任务 failed 或 timed_out 时用它定位失败原因（例如启动器缺失、模型导入失败、网格或求解报错）。
    stdout：流程脚本的标准输出；stderr：标准错误；jusmar：求解器日志。返回纯文本日志内容。
    """
    if kind not in {"stdout", "stderr", "jusmar"}:
        raise ToolError("kind 必须为 stdout / stderr / jusmar")
    settings = Settings()
    repo = _repo(settings)
    try:
        row = await repo.get(run_id)
    finally:
        await repo.close()
    if row is None:
        raise ToolError("run_id 不存在")
    run_dir = paths.task_dir(settings, run_id)
    if kind == "jusmar":
        path = paths.jusmar_log(run_dir)
    elif kind == "stderr":
        path = paths.stderr_log(run_dir)
    else:
        path = paths.stdout_log(run_dir)
    if not path.is_file():
        raise ToolError("该任务无此日志文件")
    return await asyncio.to_thread(path.read_text, encoding="utf-8")
