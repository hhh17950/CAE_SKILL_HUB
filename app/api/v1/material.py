from fastapi import APIRouter, Request
from fastapi import Response as HttpResponse

from app.api.dependencies import Call, ContainerDep, envelope
from app.schemas.common import Response
from app.schemas.material import CreateMaterialRequest, MaterialResult

router = APIRouter(tags=["material"])


@router.post(
    "/projects/{project_id}/materials",
    response_model=Response[MaterialResult],
    status_code=201,
    operation_id="create_material",
    summary="创建模拟材料，使用 Pa 和 kg/m³",
)
async def create_material(
    request: Request,
    response: HttpResponse,
    project_id: str,
    body: CreateMaterialRequest,
    call: Call,
    c: ContainerDep,
):
    result = await c.material.create_material(call, project_id, body)
    return envelope(request, result)
