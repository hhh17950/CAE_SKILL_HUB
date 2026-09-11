from fastapi import APIRouter, Request
from fastapi import Response as HttpResponse

from app.api.dependencies import Call, ContainerDep, envelope
from app.schemas.common import Response, SettingsResult
from app.schemas.post_processing import PostProcessingRequest

router = APIRouter(tags=["post_processing"])


@router.put(
    "/projects/{project_id}/post-processing-settings",
    response_model=Response[SettingsResult],
    status_code=200,
    operation_id="post_processing_settings",
    summary="替换模拟后处理配置",
)
async def post_processing_settings(
    request: Request,
    response: HttpResponse,
    project_id: str,
    body: PostProcessingRequest,
    call: Call,
    c: ContainerDep,
):
    result = await c.post_processing.post_processing_settings(call, project_id, body)
    return envelope(request, result)
