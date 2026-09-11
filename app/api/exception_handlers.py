import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.errors import DomainError
from app.schemas.common import Problem

logger = logging.getLogger(__name__)


def problem_response(request: Request, exc: DomainError):
    problem = Problem(
        title=HTTPStatus(exc.status).phrase,
        status=exc.status,
        instance=request.url.path,
        code=exc.code,
        detail=exc.detail,
        request_id=getattr(request.state, "request_id", "unavailable"),
        retryable=exc.retryable,
        errors=exc.field_errors,
    )
    headers = {"X-Request-ID": problem.request_id}
    if exc.status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        problem.model_dump(mode="json"),
        status_code=exc.status,
        media_type="application/problem+json",
        headers=headers,
    )


def install_handlers(app: FastAPI):
    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return problem_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Do not echo raw input, filesystem paths or exception context to the caller.
        fields = [
            {"field": ".".join(str(part) for part in item["loc"]), "reason": item["type"]}
            for item in exc.errors()
        ]
        null_fields = {
            "init_thermal_stress_temp",
            "material_property_temp",
            "eigen_normalize_method",
        }
        unsupported = any(
            item["loc"][-1] in null_fields and item["type"] == "none_required"
            for item in exc.errors()
            if item["loc"]
        )
        return problem_response(
            request,
            DomainError(
                "UNSUPPORTED_PARAMETER" if unsupported else "INVALID_PARAMETER",
                "当前不支持非空温度/归一化定义" if unsupported else "请求参数不符合接口定义",
                422,
                field_errors=fields,
            ),
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return problem_response(
            request, DomainError("HTTP_ERROR", str(exc.detail), exc.status_code)
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.error("request_id=%s unexpected error", request.state.request_id, exc_info=exc)
        return problem_response(
            request, DomainError("INTERNAL_ERROR", "服务内部错误，请按 request_id 排查", 500)
        )
