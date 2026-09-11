from fastapi import APIRouter, Request

from app.api.dependencies import ContainerDep, Owner, envelope
from app.schemas.common import Response
from app.schemas.operation import OperationView, TaskView

router = APIRouter(tags=["execution"])


@router.get(
    "/operations/{operation_id}",
    response_model=Response[OperationView],
    operation_id="get_operation",
)
async def get_operation(request: Request, operation_id: str, identity: Owner, c: ContainerDep):
    return envelope(request, c.shared.store.operation(identity, operation_id))


@router.get(
    "/simulation-tasks/{task_id}",
    response_model=Response[TaskView],
    operation_id="get_simulation_task",
)
async def get_simulation_task(request: Request, task_id: str, identity: Owner, c: ContainerDep):
    return envelope(request, c.shared.store.task(identity, task_id))
