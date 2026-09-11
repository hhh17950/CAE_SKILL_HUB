"""Stateful fake backend. Public resource state is maintained by Services/MemoryStore.

This backend keeps the simulated SDK invocation history and task handles. It does
not compute geometry or physics. Failure injection is constructor-only for tests.
"""

import asyncio
from pathlib import Path

from app.errors import DomainError
from app.providers.jusmar.mappings import (
    load_case_parameters,
    material_parameters,
    mesh_parameters,
    post_parameters,
    property_parameters,
)
from app.schemas.case_settings import CaseSettingsRequest
from app.schemas.geometry import GeometryResult
from app.schemas.load_case import AddLoadCaseRequest, LoadCaseResult
from app.schemas.material import CreateMaterialRequest, MaterialResult
from app.schemas.mesh import GenerateMeshRequest, MeshResult
from app.schemas.post_processing import PostProcessingRequest
from app.schemas.property import CreatePropertyRequest, PropertyResult
from app.schemas.simulation import SubmitSimulationRequest
from app.schemas.solver import SolverSettingsRequest
from app.storage.memory import new_id


class MockAdapter:
    def __init__(self, delay: float = 0.05, failures: set[str] | None = None):
        self.delay = delay
        self.failures = failures or set()
        self.history: dict[str, list[dict]] = {}
        self.tasks: dict[str, str] = {}

    def record(self, operation: str, project_id: str, parameters: dict):
        if operation in self.failures:
            raise DomainError("MOCK_EXECUTION_FAILED", f"测试夹具模拟 {operation} 失败", 502)
        self.history.setdefault(project_id, []).append(
            {"operation": operation, "parameters": {"project_id": project_id, **parameters}}
        )

    async def import_geometry(
        self,
        project_id: str,
        path: Path,
        file_name: str,
        suffix: str,
        sha256: str,
        geometry_revision: int,
    ) -> GeometryResult:
        await asyncio.sleep(self.delay)
        self.record(
            "import_geometry",
            project_id,
            {
                "geometry_file_path": str(path),
                "geometry_file_name": file_name,
                "geometry_suffix": suffix,
            },
        )
        return GeometryResult(
            project_id=project_id,
            geometry_id=new_id("geo"),
            file_name=file_name,
            file_suffix=suffix,
            sha256=sha256,
            geometry_revision=geometry_revision,
        )

    async def generate_mesh(
        self, project_id: str, parameters: GenerateMeshRequest, geometry_revision: int
    ) -> MeshResult:
        await asyncio.sleep(self.delay)
        self.record("generate_mesh", project_id, mesh_parameters(parameters))
        return MeshResult(
            project_id=project_id,
            mesh_id=new_id("mesh"),
            geometry_revision=geometry_revision,
            parameters=parameters.model_copy(deep=True),
        )

    async def create_material(
        self, project_id: str, parameters: CreateMaterialRequest
    ) -> MaterialResult:
        self.record("create_material", project_id, material_parameters(parameters))
        return MaterialResult(
            project_id=project_id,
            material_id=new_id("mat"),
            parameters=parameters.model_copy(deep=True),
        )

    async def create_3d_property(
        self, project_id: str, parameters: CreatePropertyRequest
    ) -> PropertyResult:
        self.record("create_3d_property", project_id, property_parameters(parameters))
        return PropertyResult(
            project_id=project_id,
            property_id=new_id("prop"),
            parameters=parameters.model_copy(deep=True),
        )

    async def add_load_case(
        self, project_id: str, parameters: AddLoadCaseRequest
    ) -> LoadCaseResult:
        self.record("add_load_case", project_id, load_case_parameters(parameters))
        return LoadCaseResult(
            project_id=project_id,
            load_case_id=new_id("case"),
            parameters=parameters.model_copy(deep=True),
        )

    async def solver_settings(self, project_id: str, parameters: SolverSettingsRequest) -> None:
        self.record("solver_settings", project_id, parameters.model_dump(exclude={"extensions"}))

    async def case_settings(self, project_id: str, parameters: CaseSettingsRequest) -> None:
        self.record("case_settings", project_id, parameters.model_dump(exclude={"extensions"}))

    async def post_processing_settings(
        self, project_id: str, parameters: PostProcessingRequest
    ) -> None:
        self.record("post_processing_settings", project_id, post_parameters(parameters))

    async def submit_simulation(self, project_id: str, parameters: SubmitSimulationRequest) -> str:
        self.record("submit_simulation", project_id, parameters.model_dump(exclude={"extensions"}))
        task_id = new_id("sdk_task")
        self.tasks[task_id] = "running"
        return task_id

    async def wait_simulation(self, provider_task_id: str) -> None:
        await asyncio.sleep(self.delay)
        if "simulation_result" in self.failures:
            self.tasks[provider_task_id] = "failed"
            raise DomainError("MOCK_SOLVER_FAILED", "模拟求解失败", 502)
        self.tasks[provider_task_id] = "succeeded"
