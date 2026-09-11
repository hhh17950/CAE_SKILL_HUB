from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response as HttpResponse

from app.api.dependencies import Call, Owner, envelope
from app.errors import DomainError
from app.schemas.analysis import (
    AnalysisRequest,
    AssetView,
    MockSummary,
    RunAccepted,
    RunResult,
    RunView,
    ValidationReport,
    WorkflowDescription,
)
from app.schemas.common import Response
from app.services.analysis_service import AnalysisService
from app.workflows.registry import WORKFLOWS, get_workflow

router = APIRouter()


def service(request: Request) -> AnalysisService:
    return request.app.state.analysis_service


Service = Annotated[AnalysisService, Depends(service)]


@router.post(
    "/assets",
    response_model=Response[AssetView],
    status_code=201,
    operation_id="upload_geometry_asset",
    tags=["assets"],
)
async def upload_geometry_asset(
    request: Request, identity: Owner, service: Service, file: Annotated[UploadFile, File()]
):
    return envelope(request, await service.upload(identity, file))


@router.get(
    "/assets/{asset_id}",
    response_model=Response[AssetView],
    operation_id="get_geometry_asset",
    tags=["assets"],
)
async def get_asset(asset_id: str, request: Request, identity: Owner, service: Service):
    return envelope(request, (await service.repository.asset(identity, asset_id)).view)


@router.get(
    "/workflows",
    response_model=Response[list[WorkflowDescription]],
    operation_id="list_workflows",
    tags=["workflows"],
)
async def list_workflows(request: Request, identity: Owner):
    return envelope(request, [definition.describe() for definition in WORKFLOWS.values()])


@router.get(
    "/workflows/{workflow_id}/versions/{version}",
    response_model=Response[WorkflowDescription],
    operation_id="describe_workflow",
    tags=["workflows"],
)
async def describe_workflow(workflow_id: str, version: str, request: Request, identity: Owner):
    return envelope(request, get_workflow(workflow_id, version).describe())


@router.post(
    "/analysis-validations",
    response_model=Response[ValidationReport],
    operation_id="validate_analysis",
    tags=["analysis"],
)
async def validate_analysis(
    body: AnalysisRequest, request: Request, identity: Owner, service: Service
):
    return envelope(request, await service.validate(identity, body))


@router.post(
    "/analysis-runs",
    response_model=Response[RunAccepted],
    status_code=202,
    operation_id="submit_analysis",
    tags=["analysis"],
)
async def submit_analysis(
    body: AnalysisRequest, request: Request, response: HttpResponse, call: Call, service: Service
):
    run = await service.submit(call.owner, call.idempotency_key, body, await request.json())
    url = f"/api/v1/analysis-runs/{run.run_id}"
    response.headers["Location"] = url
    response.headers["Retry-After"] = "1"
    return envelope(request, RunAccepted(run_id=run.run_id, status=run.status, status_url=url))


@router.get(
    "/analysis-runs/{run_id}",
    response_model=Response[RunView],
    operation_id="get_analysis_run",
    tags=["analysis"],
)
async def get_analysis_run(run_id: str, request: Request, identity: Owner, service: Service):
    return envelope(request, await service.repository.run(identity, run_id))


@router.post(
    "/analysis-runs/{run_id}/cancel",
    response_model=Response[RunView],
    status_code=202,
    responses={200: {"model": Response[RunView]}},
    operation_id="cancel_analysis_run",
    tags=["analysis"],
)
async def cancel_analysis_run(
    run_id: str, request: Request, response: HttpResponse, identity: Owner, service: Service
):
    run = await service.repository.cancel(identity, run_id)
    response.status_code = 202 if run.status == "cancelling" else 200
    return envelope(request, run)


@router.get(
    "/analysis-runs/{run_id}/results",
    response_model=Response[RunResult],
    operation_id="get_analysis_result",
    tags=["analysis"],
)
async def get_analysis_result(run_id: str, request: Request, identity: Owner, service: Service):
    run = await service.repository.run(identity, run_id)
    if run.status != "succeeded" or run.result is None:
        raise DomainError("RESULT_NOT_READY", "任务尚无完整可交付结果", 409)
    return envelope(request, run.result)


@router.get(
    "/analysis-runs/{run_id}/artifacts/{artifact_id}",
    operation_id="download_analysis_artifact",
    tags=["analysis"],
    response_model=MockSummary,
    response_class=HttpResponse,
    responses={
        200: {
            "description": "已校验的 Mock JSON 摘要",
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/MockSummary"}}
            },
        }
    },
)
async def download_artifact(run_id: str, artifact_id: str, identity: Owner, service: Service):
    data = await service.artifact(identity, run_id, artifact_id)
    return HttpResponse(
        data,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="summary.json"'},
    )
