from fastapi import APIRouter, Request
from fastapi import Response as HttpResponse

from app.api.dependencies import Call, ContainerDep, envelope
from app.schemas.common import Response
from app.schemas.property import CreatePropertyRequest, PropertyResult

router = APIRouter(tags=["property"])


@router.post(
    "/projects/{project_id}/properties/3d",
    response_model=Response[PropertyResult],
    status_code=201,
    operation_id="create_3d_property",
    summary="记录属性及材料引用；不执行实际几何赋值",
)
async def create_3d_property(
    request: Request,
    response: HttpResponse,
    project_id: str,
    body: CreatePropertyRequest,
    call: Call,
    c: ContainerDep,
):
    result = await c.property.create_3d_property(call, project_id, body)
    return envelope(request, result)
