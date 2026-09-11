"""Public workflow contract. Models contain only checks that do not require I/O."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from app.schemas.common import ApiModel, ExecutionError, Identifier, Name

RunStatus = Literal[
    "queued", "running", "cancelling", "succeeded", "failed", "cancelled", "timed_out", "unknown"
]
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "timed_out", "unknown"}


class GeometryInput(ApiModel):
    geometry_asset_id: Identifier
    length_unit: Literal["m", "cm", "mm"]


class MeshInput(ApiModel):
    mesh_type: Literal["tetrahedron"] = "tetrahedron"
    element_order: Literal["first_order"] = "first_order"
    mesh_density: Literal["low", "medium", "high"] = "medium"


class MaterialInput(ApiModel):
    material_name: Name
    young_modulus_pa: Annotated[float, Field(strict=True, gt=0)]
    poisson_ratio: Annotated[float, Field(strict=True, gt=-1, lt=0.5)]
    density_kg_m3: Annotated[float, Field(strict=True, gt=0)]
    constitutive_model: Literal["linear_elastic_isotropic"] = "linear_elastic_isotropic"
    assignment: Literal["all_solids"] = "all_solids"


class ModalInput(ApiModel):
    geometry: GeometryInput
    material: MaterialInput
    mesh: MeshInput = Field(default_factory=MeshInput)
    boundary_condition: Literal["free"] = "free"
    eigen_method: Literal["lanczos"] = "lanczos"
    mode_count: Annotated[int, Field(strict=True, ge=1, le=100)] = 10
    process_count: Annotated[int, Field(strict=True, ge=1, le=64)] = 1


class ModalAnalysisRequest(ApiModel):
    workflow_id: Literal["modal_analysis"]
    workflow_version: Literal["1.0"]
    input: ModalInput


# One variant today: use the concrete model, not Union[T], which Python collapses.
# When another workflow ships, publish an explicit discriminated union here.
AnalysisRequest = ModalAnalysisRequest


class ValidationCheck(ApiModel):
    name: str
    status: Literal["passed", "failed", "deferred"]
    code: str
    detail: str


class ValidationReport(ApiModel):
    valid: bool
    scope: Literal["submission"] = "submission"
    checks: list[ValidationCheck]
    normalized_request: ModalAnalysisRequest
    execution_mode: Literal["mock"] = "mock"


class AssetView(ApiModel):
    asset_id: str
    file_name: str
    suffix: str
    size_bytes: int
    sha256: str
    created_at: datetime


class StepView(ApiModel):
    name: str
    status: Literal["pending", "running", "succeeded", "failed", "cancelled", "unknown"]


class ArtifactView(ApiModel):
    artifact_id: str
    kind: Literal["mock_summary"] = "mock_summary"
    media_type: Literal["application/json"] = "application/json"
    size_bytes: int
    sha256: str
    download_url: str


class RunResult(ApiModel):
    result_type: Literal["modal_mock_summary"] = "modal_mock_summary"
    synthetic: Literal[True] = True
    quality_status: Literal["not_evaluated"] = "not_evaluated"
    project_id: str
    material_id: str
    load_case_id: str
    solver_task_id: str
    requested_mode_count: int
    completed_steps: list[str]
    artifacts: list[ArtifactView]
    notice: str = "仅验证自动化协议；没有计算频率、位移、反力或任何物理结果。"


class MockSummary(ApiModel):
    execution_mode: Literal["mock"] = "mock"
    synthetic: Literal[True] = True
    run_id: str
    attempt_id: str
    request: ModalAnalysisRequest
    geometry_sha256: str
    project_id: str
    material_id: str
    load_case_id: str
    solver_task_id: str
    notice: str = "没有解析 CAD 或计算任何物理结果；仅用于协议测试。"


class RunView(ApiModel):
    run_id: str
    workflow_id: Literal["modal_analysis"]
    workflow_version: Literal["1.0"]
    implementation_version: str
    execution_mode: Literal["mock"] = "mock"
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    current_step: str | None = None
    steps: list[StepView] = Field(default_factory=list)
    cancel_requested: bool = False
    attempt_id: str | None = None
    worker_id: str | None = None
    request: ModalAnalysisRequest
    input_asset_sha256: str
    runtime_versions: dict[str, str] = Field(default_factory=dict)
    result: RunResult | None = None
    error: ExecutionError | None = None


class RunAccepted(ApiModel):
    run_id: str
    status: RunStatus
    status_url: str


class WorkflowDescription(ApiModel):
    workflow_id: str
    workflow_version: str
    implementation_version: str
    title: str
    execution_mode: Literal["mock"] = "mock"
    input_schema: dict
    result_schema: dict
    limitations: list[str]
    steps: list[str]
