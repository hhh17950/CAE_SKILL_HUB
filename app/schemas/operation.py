from datetime import datetime

from app.schemas.common import ApiModel, ExecutionError, Status
from app.schemas.geometry import GeometryResult
from app.schemas.mesh import MeshResult
from app.schemas.project import ProjectView
from app.schemas.simulation import SubmitSimulationRequest


class OperationView(ApiModel):
    operation_id: str
    operation: str
    project_id: str | None = None
    status: Status = "queued"
    result: GeometryResult | MeshResult | None = None
    error: ExecutionError | None = None
    created_at: datetime
    updated_at: datetime


class TaskView(ApiModel):
    task_id: str
    project_id: str
    status: Status = "queued"
    input_snapshot: ProjectView
    parameters: SubmitSimulationRequest
    error: ExecutionError | None = None
    created_at: datetime
    updated_at: datetime
