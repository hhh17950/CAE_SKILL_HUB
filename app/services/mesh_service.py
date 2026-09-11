import asyncio

from app.errors import DomainError, unsupported
from app.execution.runner import execution_error
from app.schemas.common import OperationAccepted
from app.schemas.mesh import GenerateMeshRequest
from app.schemas.operation import OperationView
from app.services.context import CallContext, Services
from app.storage.memory import StoredProject, new_id, now


class MeshService:
    def __init__(self, services: Services):
        self.s = services

    async def generate_mesh(self, call: CallContext, project_id: str, request: GenerateMeshRequest):
        if request.mesh_option != "conforming":
            raise unsupported("mesh_option")
        if request.advanced_option_enable:
            raise unsupported("advanced_option_enable")

        async def execute(project: StoredProject):
            if project.view.geometry is None:
                raise DomainError("PROJECT_STATE_CONFLICT", "请先完成几何导入", 409)
            self.s.store.check_capacity()
            operation = OperationView(
                operation_id=new_id("op"),
                operation="generate_mesh",
                project_id=project_id,
                created_at=now(),
                updated_at=now(),
            )
            self.s.store.operations[operation.operation_id] = (call.owner, operation)
            project.busy = operation.operation_id
            self.s.runner.start(lambda: self._run(project, request, operation))
            return OperationAccepted(
                operation_id=operation.operation_id,
                status_url=f"/api/v1/operations/{operation.operation_id}",
            )

        return await self.s.mutate(call, project_id, "generate_mesh", request, execute)

    async def _run(
        self, project: StoredProject, request: GenerateMeshRequest, operation: OperationView
    ):
        operation.status = "running"
        operation.updated_at = now()
        try:
            result = await self.s.provider.generate_mesh(
                project.view.project_id, request, project.view.geometry.geometry_revision
            )
            project.view.mesh = result
            project.view.revision += 1
            operation.result = result
            operation.status = "succeeded"
        except asyncio.CancelledError:
            operation.status = "unknown"
            raise
        except Exception as exc:
            operation.error = execution_error(exc)
            operation.status = "failed" if isinstance(exc, DomainError) else "unknown"
        finally:
            operation.updated_at = now()
            if operation.status in {"succeeded", "failed"}:
                project.busy = None
