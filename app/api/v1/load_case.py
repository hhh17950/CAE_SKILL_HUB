from fastapi import APIRouter, Request
from fastapi import Response as HttpResponse

from app.api.dependencies import Call, ContainerDep, envelope
from app.schemas.common import Response
from app.schemas.load_case import AddLoadCaseRequest, LoadCaseResult

router = APIRouter(tags=["load_case"])


@router.post(
    "/projects/{project_id}/load-cases",
    response_model=Response[LoadCaseResult],
    status_code=201,
    operation_id="add_load_case",
    summary="创建模拟工况；三个开关仅记录，不定义物理载荷",
)
async def add_load_case(
    request: Request,
    response: HttpResponse,
    project_id: str,
    body: AddLoadCaseRequest,
    call: Call,
    c: ContainerDep,
):
    result = await c.load_case.add_load_case(call, project_id, body)
    return envelope(request, result)
