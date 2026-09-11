from fastapi import APIRouter, Request
from fastapi import Response as HttpResponse

from app.api.dependencies import Call, ContainerDep, envelope
from app.schemas.common import Response, TaskAccepted
from app.schemas.simulation import SubmitSimulationRequest

router = APIRouter(tags=["simulation"])


@router.post(
    "/projects/{project_id}/simulation-tasks",
    response_model=Response[TaskAccepted],
    status_code=202,
    operation_id="submit_simulation",
    summary="提交模拟运行并返回任务；不产生物理结果",
)
async def submit_simulation(
    request: Request,
    response: HttpResponse,
    project_id: str,
    body: SubmitSimulationRequest,
    call: Call,
    c: ContainerDep,
):
    result = await c.simulation.submit_simulation(call, project_id, body)
    response.headers["Location"] = result.status_url
    return envelope(request, result)
