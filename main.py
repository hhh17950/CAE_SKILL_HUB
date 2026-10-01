"""FastAPI entry point for the single guie2 script flow."""

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from api.body_limit import RequestBodyLimit
from api.errors import DomainError
from api.exception_handlers import install_handlers, problem_response
from api.openapi import install_openapi
from api.router import router
from core.config import Settings
from core.logging import configure_logging
from storage.repository import RunRepository

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"


def load_mcp_server(settings: Settings):
    """Return ``(server, asgi_app)`` for /mcp, or None when MCP is disabled or unusable.

    The MCP module is imported here rather than inside the lifespan so the reason for a
    missing /mcp route is reported at startup instead of at the first request.
    """
    if not settings.mcp_enabled:
        return None
    try:
        from services.mcp_server import build_mcp_server, http_app
    except ImportError as exc:
        logger.error(
            "CAE_MCP_ENABLED is set but the MCP dependency is unusable; %s is NOT mounted: %s",
            MCP_PATH,
            exc,
        )
        return None
    server = build_mcp_server(settings)
    # streamable_http_app() also creates the session manager the lifespan below starts.
    return server, http_app(server, settings)


async def serve_mcp_session_manager(server, ready: asyncio.Event, stop: asyncio.Event) -> None:
    """Own the streamable HTTP session manager for as long as the API serves requests.

    mount() never runs a sub-application lifespan, so the session manager would reject every
    request unless it is started here. It runs in this dedicated task because an anyio cancel
    scope must be entered and exited by the same task, while an ASGI lifespan generator may be
    closed from another one (pytest-asyncio finalizes its client fixture that way).
    """
    try:
        async with server.session_manager.run():
            ready.set()
            await stop.wait()
    except Exception:  # pragma: no cover - startup/shutdown failure is reported, not raised
        logger.exception("MCP session manager stopped unexpectedly")
        ready.set()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    mcp = load_mcp_server(settings)
    mcp_server, mcp_asgi = mcp if mcp is not None else (None, None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.service_log_root, "api")
        guie_store = RunRepository(settings.database_path)
        stop_mcp = asyncio.Event()
        mcp_task: asyncio.Task | None = None
        try:
            await guie_store.initialize()
            app.state.guie_store = guie_store
            logger.info("CAE guie2 test API started; launch the separate Worker")
            if mcp_server is not None:
                ready = asyncio.Event()
                mcp_task = asyncio.create_task(
                    serve_mcp_session_manager(mcp_server, ready, stop_mcp)
                )
                await ready.wait()
                logger.info("MCP server mounted at %s", MCP_PATH)
            yield
        finally:
            if mcp_task is not None:
                stop_mcp.set()
                await mcp_task
            await guie_store.close()

    app = FastAPI(
        title="CAE SkillHub — guie2 Script Service",
        version="0.3.0-test",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        description="固定 guie2 测试脚本的异步任务服务。以退出码判断脚本运行状态；"
        "当前不启动真实 GUI、不计算物理结果。",
    )
    app.state.settings = settings
    install_handlers(app)
    app.add_middleware(RequestBodyLimit, max_bytes=settings.max_request_bytes)

    @app.middleware("http")
    async def request_metadata(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            supplied if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", supplied) else f"req_{uuid4().hex}"
        )
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > settings.max_request_bytes):
            return problem_response(
                request, DomainError("REQUEST_TOO_LARGE", "请求体超过服务限制", 413)
            )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Execution-Mode"] = "test-script"
        return response

    app.include_router(router)
    # Mount ONLY documentation assets, never the sibling static/logs directory.
    app.mount(
        "/static/docs",
        StaticFiles(directory=Path(__file__).resolve().parent / "static" / "docs"),
        name="swagger-assets",
    )

    install_openapi(app)
    if mcp_asgi is not None:
        # The MCP app already routes MCP_PATH internally, so mounting it under MCP_PATH
        # again would expose MCP_PATH + MCP_PATH instead. Mounted last at the root, it only
        # sees requests that no earlier route matched.
        app.mount("/", mcp_asgi)
    return app


if __name__ == "__main__":
    import uvicorn

    # 与 start.sh 共用同一组开关，默认只监听本机回环地址。要让其他机器访问，用
    # ``CAE_API_HOST=0.0.0.0 python main.py``，或直接 ``./start.sh start``（默认即 0.0.0.0）。
    # 注意监听地址与对外公布地址是两件事：CAE_PUBLIC_BASE_URL 必须是调用方能访问的地址。
    uvicorn.run(
        create_app(),
        host=os.environ.get("CAE_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("CAE_API_PORT", "8000")),
    )
