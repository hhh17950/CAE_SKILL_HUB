from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from app.errors import DomainError
from app.execution.protocol import JobInput
from app.providers.interface import CaeProvider
from app.schemas.analysis import (
    ModalAnalysisRequest,
    RunResult,
    RunView,
    StepView,
    WorkflowDescription,
)
from app.workflows.modal import execute_modal, verify_modal_result

MODAL_STEPS = (
    "import_geometry",
    "generate_mesh",
    "create_material",
    "create_3d_property",
    "add_load_case",
    "solver_settings",
    "case_settings",
    "post_processing_settings",
    "submit_simulation",
    "monitor_simulation",
    "collect_results",
)


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    version: str
    implementation_version: str
    title: str
    request_model: type[BaseModel]
    result_model: type[BaseModel]
    steps: tuple[str, ...]
    limitations: tuple[str, ...]
    execute: Callable[
        [CaeProvider, JobInput, Path, list[StepView], Callable[[], None]], Awaitable[RunResult]
    ]
    verify_result: Callable[[RunView, RunResult, dict], None]

    def describe(self) -> WorkflowDescription:
        return WorkflowDescription(
            workflow_id=self.workflow_id,
            workflow_version=self.version,
            implementation_version=self.implementation_version,
            title=self.title,
            input_schema=self.request_model.model_json_schema(),
            result_schema=self.result_model.model_json_schema(),
            limitations=list(self.limitations),
            steps=list(self.steps),
        )


MODAL = WorkflowDefinition(
    workflow_id="modal_analysis",
    version="1.0",
    implementation_version="modal-mock-1",
    title="自由模态分析流程（Mock）",
    request_model=ModalAnalysisRequest,
    result_model=RunResult,
    steps=MODAL_STEPS,
    execute=execute_modal,
    verify_result=verify_modal_result,
    limitations=(
        "仅支持自由边界、单一各向同性线弹性材料、全部实体统一赋材的协议模拟。",
        "不解析 CAD，不验证拓扑连通性，不执行真实单位转换和 CAE 求解。",
        "只输出 Mock 执行摘要；没有频率、位移、反力等物理结果。",
        "不支持载荷、接触、局部赋材、预应力和中途交互。",
    ),
)
WORKFLOWS = {(MODAL.workflow_id, MODAL.version): MODAL}


def get_workflow(workflow_id: str, version: str) -> WorkflowDefinition:
    definition = WORKFLOWS.get((workflow_id, version))
    if definition is None:
        raise DomainError("WORKFLOW_NOT_FOUND", "工作流或版本不受支持", 404)
    return definition
