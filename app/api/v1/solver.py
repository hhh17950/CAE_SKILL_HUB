from fastapi import APIRouter, Request
from fastapi import Response as HttpResponse

from app.api.dependencies import Call, ContainerDep, envelope
from app.schemas.common import Response, SettingsResult
from app.schemas.solver import SolverSettingsRequest

router = APIRouter(tags=["solver"])


@router.put(
    "/projects/{project_id}/solver-settings",
    response_model=Response[SettingsResult],
    status_code=200,
    operation_id="solver_settings",
    summary="替换求解器配置，仅接受 Mock 夹具值",
)
async def solver_settings(
    request: Request,
    response: HttpResponse,
    project_id: str,
    body: SolverSettingsRequest,
    call: Call,
    c: ContainerDep,
):
    result = await c.solver.solver_settings(call, project_id, body)
    return envelope(request, result)
