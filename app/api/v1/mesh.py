from fastapi import APIRouter, Request
from fastapi import Response as HttpResponse

from app.api.dependencies import Call, ContainerDep, envelope
from app.schemas.common import OperationAccepted, Response
from app.schemas.mesh import GenerateMeshRequest

router = APIRouter(tags=["mesh"])


@router.post(
    "/projects/{project_id}/meshes",
    response_model=Response[OperationAccepted],
    status_code=202,
    operation_id="generate_mesh",
    summary="模拟生成网格；必须先完成几何导入",
)
async def generate_mesh(
    request: Request,
    response: HttpResponse,
    project_id: str,
    body: GenerateMeshRequest,
    call: Call,
    c: ContainerDep,
):
    result = await c.mesh.generate_mesh(call, project_id, body)
    response.headers["Location"] = result.status_url
    return envelope(request, result)
