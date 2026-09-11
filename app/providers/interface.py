from pathlib import Path
from typing import Protocol

from app.schemas.case_settings import CaseSettingsRequest
from app.schemas.geometry import GeometryResult
from app.schemas.load_case import AddLoadCaseRequest, LoadCaseResult
from app.schemas.material import CreateMaterialRequest, MaterialResult
from app.schemas.mesh import GenerateMeshRequest, MeshResult
from app.schemas.post_processing import PostProcessingRequest
from app.schemas.property import CreatePropertyRequest, PropertyResult
from app.schemas.simulation import SubmitSimulationRequest
from app.schemas.solver import SolverSettingsRequest


class CaeProvider(Protocol):
    async def import_geometry(
        self,
        project_id: str,
        path: Path,
        file_name: str,
        suffix: str,
        sha256: str,
        geometry_revision: int,
    ) -> GeometryResult: ...
    async def generate_mesh(
        self, project_id: str, parameters: GenerateMeshRequest, geometry_revision: int
    ) -> MeshResult: ...
    async def create_material(
        self, project_id: str, parameters: CreateMaterialRequest
    ) -> MaterialResult: ...
    async def create_3d_property(
        self, project_id: str, parameters: CreatePropertyRequest
    ) -> PropertyResult: ...
    async def add_load_case(
        self, project_id: str, parameters: AddLoadCaseRequest
    ) -> LoadCaseResult: ...
    async def solver_settings(self, project_id: str, parameters: SolverSettingsRequest) -> None: ...
    async def case_settings(self, project_id: str, parameters: CaseSettingsRequest) -> None: ...
    async def post_processing_settings(
        self, project_id: str, parameters: PostProcessingRequest
    ) -> None: ...
    async def submit_simulation(
        self, project_id: str, parameters: SubmitSimulationRequest
    ) -> str: ...
    async def wait_simulation(self, provider_task_id: str) -> None: ...
