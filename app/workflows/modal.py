"""Trusted nine-step orchestration, plus solve monitoring and result collection.

Executed inside the isolated Mock process. The actual embedded GUI Python runtime
must be probed before deciding which of these Python modules it can reuse.
"""

import hashlib
from collections.abc import Callable
from pathlib import Path

from app.execution.protocol import JobInput, atomic_json
from app.providers.interface import CaeProvider
from app.schemas.analysis import ArtifactView, MockSummary, RunResult, RunView, StepView
from app.schemas.case_settings import CaseSettingsRequest
from app.schemas.load_case import AddLoadCaseRequest
from app.schemas.material import CreateMaterialRequest
from app.schemas.mesh import GenerateMeshRequest
from app.schemas.post_processing import PostProcessingRequest
from app.schemas.property import CreatePropertyRequest
from app.schemas.simulation import SubmitSimulationRequest
from app.schemas.solver import SolverSettingsRequest
from app.storage.memory import new_id


async def execute_modal(
    provider: CaeProvider,
    command: JobInput,
    directory: Path,
    steps: list[StepView],
    report: Callable[[], None],
) -> RunResult:
    parameters = command.request.input
    project_id = new_id("project")

    async def step(name, operation):
        state = next(s for s in steps if s.name == name)
        state.status = "running"
        report()
        try:
            result = await operation()
        except Exception:
            state.status = "failed"
            report()
            raise
        state.status = "succeeded"
        report()
        return result

    await step(
        "import_geometry",
        lambda: provider.import_geometry(
            project_id,
            directory / command.geometry_path,
            "geometry" + command.geometry_suffix,
            command.geometry_suffix,
            command.geometry_sha256,
            1,
        ),
    )
    await step(
        "generate_mesh",
        lambda: provider.generate_mesh(
            project_id,
            GenerateMeshRequest(**parameters.mesh.model_dump()),
            1,
        ),
    )
    material = await step(
        "create_material",
        lambda: provider.create_material(
            project_id,
            CreateMaterialRequest(
                material_name=parameters.material.material_name,
                young_modulus=parameters.material.young_modulus_pa,
                poisson_ratio=parameters.material.poisson_ratio,
                density=parameters.material.density_kg_m3,
            ),
        ),
    )
    await step(
        "create_3d_property",
        lambda: provider.create_3d_property(
            project_id,
            CreatePropertyRequest(material_ref=material.material_id),
        ),
    )
    load_case = await step(
        "add_load_case",
        lambda: provider.add_load_case(
            project_id,
            AddLoadCaseRequest(),
        ),
    )
    await step(
        "solver_settings",
        lambda: provider.solver_settings(
            project_id,
            SolverSettingsRequest(solver_type="modal", linear_solver_type="program_controlled"),
        ),
    )
    await step(
        "case_settings",
        lambda: provider.case_settings(
            project_id,
            CaseSettingsRequest(
                selected_load_case=load_case.load_case_id, eigen_count_value=parameters.mode_count
            ),
        ),
    )
    await step(
        "post_processing_settings",
        lambda: provider.post_processing_settings(
            project_id,
            PostProcessingRequest(),
        ),
    )
    task_id = await step(
        "submit_simulation",
        lambda: provider.submit_simulation(
            project_id,
            SubmitSimulationRequest(
                process_num=parameters.process_count,
                analyze_load_case_list=[load_case.load_case_id],
            ),
        ),
    )
    await step("monitor_simulation", lambda: provider.wait_simulation(task_id))

    async def collect():
        summary = MockSummary(
            run_id=command.run_id,
            attempt_id=command.attempt_id,
            request=command.request,
            geometry_sha256=command.geometry_sha256,
            project_id=project_id,
            material_id=material.material_id,
            load_case_id=load_case.load_case_id,
            solver_task_id=task_id,
        )
        target = directory / "summary.json"
        atomic_json(target, summary)
        body = target.read_bytes()
        artifact = ArtifactView(
            artifact_id="summary",
            size_bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            download_url=f"/api/v1/analysis-runs/{command.run_id}/artifacts/summary",
        )
        return RunResult(
            project_id=project_id,
            material_id=material.material_id,
            load_case_id=load_case.load_case_id,
            solver_task_id=task_id,
            requested_mode_count=parameters.mode_count,
            completed_steps=[s.name for s in steps],
            artifacts=[artifact],
        )

    return await step("collect_results", collect)


def verify_modal_result(run: RunView, result: RunResult, document: dict):
    summary = MockSummary.model_validate(document)
    if result.requested_mode_count != run.request.input.mode_count:
        raise ValueError("modal count does not match the request")
    if (
        summary.run_id != run.run_id
        or summary.attempt_id != run.attempt_id
        or summary.request != run.request
        or summary.geometry_sha256 != run.input_asset_sha256
    ):
        raise ValueError("result artifact does not match the submitted input")
    for field in ("project_id", "material_id", "load_case_id", "solver_task_id"):
        if getattr(summary, field) != getattr(result, field):
            raise ValueError("result references do not match the artifact")
