from fastapi import APIRouter, Request
from fastapi import Response as HttpResponse

from app.api.dependencies import Call, ContainerDep, envelope
from app.schemas.case_settings import CaseSettingsRequest
from app.schemas.common import Response, SettingsResult

router = APIRouter(tags=["case_settings"])


@router.put(
    "/projects/{project_id}/case-settings",
    response_model=Response[SettingsResult],
    status_code=200,
    operation_id="case_settings",
    summary="替换模态分析配置，引用当前工程工况",
)
async def case_settings(
    request: Request,
    response: HttpResponse,
    project_id: str,
    body: CaseSettingsRequest,
    call: Call,
    c: ContainerDep,
):
    result = await c.case_settings.case_settings(call, project_id, body)
    return envelope(request, result)
