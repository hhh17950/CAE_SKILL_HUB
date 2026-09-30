"""FastAPI entry point for the single guie2 script flow."""

import logging
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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.service_log_root, "api")
        guie_store = RunRepository(settings.database_path)
        try:
            await guie_store.initialize()
            app.state.guie_store = guie_store
            logger.info("CAE guie2 test API started; launch the separate Worker")
            yield
        finally:
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
    if settings.mcp_enabled:
        try:
            from services.mcp_server import fastmcp_app as _mcp

            app.mount("/mcp", _mcp.streamable_http_app())
            logger.info("MCP server mounted as /mcp")
        except ImportError:
            logger.error("CAE_MCP_ENABLED is set but the `mcp` dependency is missing; /mcp is NOT mounted")
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
