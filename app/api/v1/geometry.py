from fastapi import APIRouter, Request
from fastapi import Response as HttpResponse

from app.api.dependencies import Call, ContainerDep, envelope
from app.schemas.common import OperationAccepted, Response
from app.schemas.geometry import ImportGeometryRequest

router = APIRouter(tags=["geometry"])


@router.post(
    "/geometry-imports",
    response_model=Response[OperationAccepted],
    status_code=202,
    operation_id="import_geometry",
    summary="导入测试几何，返回异步操作；成功后查询 project_id",
)
async def import_geometry(
    request: Request,
    response: HttpResponse,
    body: ImportGeometryRequest,
    call: Call,
    c: ContainerDep,
):
    result = await c.geometry.import_geometry(call, body)
    response.headers["Location"] = result.status_url
    return envelope(request, result)
