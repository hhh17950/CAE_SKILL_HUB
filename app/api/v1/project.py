from fastapi import APIRouter, Request

from app.api.dependencies import ContainerDep, Owner, envelope
from app.schemas.common import Response
from app.schemas.project import ProjectView

router = APIRouter(tags=["projects"])


@router.get(
    "/projects/{project_id}",
    response_model=Response[ProjectView],
    operation_id="get_project",
    summary="查询 Mock 工程、生效参数及资源引用",
)
async def get_project(request: Request, project_id: str, identity: Owner, c: ContainerDep):
    return envelope(
        request, c.shared.store.project(identity, project_id).view.model_copy(deep=True)
    )
