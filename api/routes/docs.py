"""Offline Swagger UI. No runtime CDN, remote favicon or online validator."""

from fastapi import APIRouter, Request
from fastapi.openapi.docs import get_swagger_ui_html

router = APIRouter()


@router.get("/docs", include_in_schema=False)
async def swagger_docs(request: Request):
    root = request.scope.get("root_path", "").rstrip("/")
    return get_swagger_ui_html(
        openapi_url=f"{root}{request.app.openapi_url}",
        title=f"{request.app.title} - Swagger UI",
        swagger_js_url=f"{root}/static/docs/swagger-ui-bundle.js",
        swagger_css_url=f"{root}/static/docs/swagger-ui.css",
        swagger_favicon_url=f"{root}/static/docs/favicon-32x32.png",
        swagger_ui_parameters={"validatorUrl": None},
    )
