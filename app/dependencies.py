from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.execution.runner import Runner
from app.providers.mock import MockAdapter
from app.services.case_settings_service import CaseSettingsService
from app.services.context import Services
from app.services.file_service import FileService
from app.services.geometry_service import GeometryService
from app.services.load_case_service import LoadCaseService
from app.services.material_service import MaterialService
from app.services.mesh_service import MeshService
from app.services.post_processing_service import PostProcessingService
from app.services.property_service import PropertyService
from app.services.simulation_service import SimulationService
from app.services.solver_service import SolverService
from app.storage.memory import MemoryStore


@dataclass
class Container:
    shared: Services
    files: FileService
    geometry: GeometryService
    mesh: MeshService
    material: MaterialService
    property: PropertyService
    load_case: LoadCaseService
    solver: SolverService
    case_settings: CaseSettingsService
    post_processing: PostProcessingService
    simulation: SimulationService


def build_container(
    settings: Settings, upload_directory: Path, provider: MockAdapter | None = None
):
    if settings.provider != "mock":
        raise RuntimeError("真实 jusmar_app SDK 尚未接入；禁止自动回退 Mock")
    shared = Services(
        settings,
        provider or MockAdapter(settings.mock_delay_seconds),
        MemoryStore(settings.max_records),
        Runner(),
    )
    files = FileService(shared, upload_directory)
    return Container(
        shared,
        files,
        GeometryService(shared, files),
        MeshService(shared),
        MaterialService(shared),
        PropertyService(shared),
        LoadCaseService(shared),
        SolverService(shared),
        CaseSettingsService(shared),
        PostProcessingService(shared),
        SimulationService(shared),
    )
