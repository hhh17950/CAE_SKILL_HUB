from pydantic import Field

from app.schemas.case_settings import CaseSettingsRequest
from app.schemas.common import ApiModel
from app.schemas.geometry import GeometryResult
from app.schemas.load_case import LoadCaseResult
from app.schemas.material import MaterialResult
from app.schemas.mesh import MeshResult
from app.schemas.post_processing import PostProcessingRequest
from app.schemas.property import PropertyResult
from app.schemas.solver import SolverSettingsRequest


class ProjectView(ApiModel):
    project_id: str
    revision: int = 1
    geometry: GeometryResult | None = None
    mesh: MeshResult | None = None
    materials: list[MaterialResult] = Field(default_factory=list)
    properties: list[PropertyResult] = Field(default_factory=list)
    load_cases: list[LoadCaseResult] = Field(default_factory=list)
    solver_settings: SolverSettingsRequest | None = None
    case_settings: CaseSettingsRequest | None = None
    post_processing_settings: PostProcessingRequest | None = None
    task_ids: list[str] = Field(default_factory=list)
