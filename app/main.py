import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI, Request

from app.api.body_limit import RequestBodyLimit
from app.api.exception_handlers import install_handlers, problem_response
from app.api.openapi import install_openapi
from app.api.v1.analysis import router as analysis_router
from app.api.v1.router import ERROR_RESPONSES, router
from app.config import Settings
from app.dependencies import build_container
from app.errors import DomainError
from app.providers.mock import MockAdapter
from app.services.analysis_service import AnalysisService
from app.storage.jobs import JobRepository
from app.storage.memory import new_id

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, provider: MockAdapter | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.provider != "mock":
            raise RuntimeError("真实 SDK 尚未接入；CAE_PROVIDER 必须为 mock")
        repository = JobRepository(settings.database_path, settings.max_records)
        await repository.initialize()
        app.state.analysis_service = AnalysisService(settings, repository)
        if not settings.enable_legacy_api:
            logger.warning("CAE MOCK workflow API: durable jobs; start the separate Mock Worker")
            yield
            return
        settings.upload_root.mkdir(parents=True, exist_ok=True)
        # Only this process's generated staging directory is removed at shutdown.
        with TemporaryDirectory(prefix="mock-session-", dir=settings.upload_root) as directory:
            container = build_container(settings, Path(directory).resolve(), provider)
            app.state.container = container
            logger.warning(
                "CAE MOCK mode: no real computation, no restart recovery, use one worker"
            )
            try:
                yield
            finally:
                await container.shared.runner.close()

    app = FastAPI(
        title="CAE Skill — Workflow Service",
        version="0.2.0-draft",
        lifespan=lifespan,
        description="类型化 CAE 工作流、持久化任务与独立 Mock Worker。仅输出协议执行摘要，"
        "不解析 CAD、不启动真实 GUI、不计算物理结果。客户合同仍为待真实环境验证的草案。",
    )
    app.state.settings = settings
    install_handlers(app)
    app.add_middleware(RequestBodyLimit, max_bytes=settings.max_file_bytes + 65536)

    @app.middleware("http")
    async def request_metadata(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            supplied if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", supplied) else new_id("req")
        )
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > settings.max_file_bytes + 65536):
            return problem_response(
                request, DomainError("REQUEST_TOO_LARGE", "请求体超过服务限制", 413)
            )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Execution-Mode"] = "mock"
        return response

    app.include_router(analysis_router, prefix="/api/v1", responses=ERROR_RESPONSES)
    if settings.enable_legacy_api:
        app.include_router(router, prefix="/api/v1", deprecated=True)

    @app.get("/healthz", tags=["health"])
    async def health():
        return {"status": "ok", "execution_mode": "mock", "storage_mode": "sqlite", "worker_mode": "separate_process"}

    install_openapi(app)
    return app
