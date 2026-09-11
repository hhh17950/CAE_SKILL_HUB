import asyncio

from app.errors import DomainError, unsupported
from app.execution.runner import execution_error
from app.schemas.common import TaskAccepted
from app.schemas.operation import TaskView
from app.schemas.simulation import SubmitSimulationRequest
from app.services.context import CallContext, Services
from app.storage.memory import StoredProject, new_id, now


class SimulationService:
    def __init__(self, services: Services):
        self.s = services

    async def submit_simulation(
        self, call: CallContext, project_id: str, request: SubmitSimulationRequest
    ):
        if request.process_num > self.s.settings.max_process_num:
            raise unsupported("process_num", "超过本部署的模拟进程数上限")

        async def execute(project: StoredProject):
            view = project.view
            if not (
                view.geometry
                and view.mesh
                and view.properties
                and view.solver_settings
                and view.case_settings
                and view.post_processing_settings
            ):
                raise DomainError(
                    "PROJECT_STATE_CONFLICT", "Mock 模态示例所需的网格、属性或配置尚未完成", 409
                )
            if view.mesh.geometry_revision != view.geometry.geometry_revision:
                raise DomainError("STALE_MESH", "网格不属于当前几何版本", 409)
            known = {c.load_case_id for c in view.load_cases}
            if not set(request.analyze_load_case_list).issubset(known):
                raise DomainError(
                    "RESOURCE_REFERENCE_CONFLICT", "提交工况不存在或不属于当前工程", 409
                )
            # v0.1 explicitly has one active analysis configuration, not one per load case.
            if request.analyze_load_case_list != [view.case_settings.selected_load_case]:
                raise unsupported("analyze_load_case_list", "当前仅支持提交已配置的一个工况")
            self.s.store.check_capacity()
            task = TaskView(
                task_id=new_id("task"),
                project_id=project_id,
                input_snapshot=view.model_copy(deep=True),
                parameters=request.model_copy(deep=True),
                created_at=now(),
                updated_at=now(),
            )
            self.s.store.tasks[task.task_id] = (call.owner, task)
            project.busy = task.task_id
            view.task_ids.append(task.task_id)
            self.s.runner.start(lambda: self._run(project, task))
            return TaskAccepted(
                task_id=task.task_id, status_url=f"/api/v1/simulation-tasks/{task.task_id}"
            )

        return await self.s.mutate(call, project_id, "submit_simulation", request, execute)

    async def _run(self, project: StoredProject, task: TaskView):
        task.status = "running"
        task.updated_at = now()
        try:
            provider_task_id = await self.s.provider.submit_simulation(
                task.project_id, task.parameters
            )
            await self.s.provider.wait_simulation(provider_task_id)
            task.status = "succeeded"
        except asyncio.CancelledError:
            task.status = "unknown"
            raise
        except Exception as exc:
            task.error = execution_error(exc)
            task.status = "failed" if isinstance(exc, DomainError) else "unknown"
        finally:
            task.updated_at = now()
            if task.status in {"succeeded", "failed"}:
                project.busy = None
