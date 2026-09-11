import asyncio

from app.errors import DomainError
from app.execution.runner import execution_error
from app.schemas.common import OperationAccepted
from app.schemas.geometry import ImportGeometryRequest
from app.schemas.operation import OperationView
from app.schemas.project import ProjectView
from app.services.context import CallContext, Services
from app.services.file_service import FileService
from app.storage.memory import StoredFile, StoredProject, new_id, now


class GeometryService:
    def __init__(self, services: Services, files: FileService):
        self.s = services
        self.files = files

    async def import_geometry(self, call: CallContext, request: ImportGeometryRequest):
        self.s.validate_extensions(request)
        if request.project_id:
            self.s.store.project(call.owner, request.project_id)

        async def execute():
            file = await self.files.resolve(call.owner, request.source)
            self.s.store.check_capacity()
            project_id = request.project_id or new_id("prj")
            if request.project_id is None:
                project = StoredProject(
                    call.owner, ProjectView(project_id=project_id), asyncio.Lock()
                )
                self.s.store.projects[project_id] = project
            else:
                project = self.s.store.project(call.owner, project_id)
            async with project.lock:
                self.s.store.require_idle(project)
                operation = OperationView(
                    operation_id=new_id("op"),
                    operation="import_geometry",
                    project_id=project_id,
                    created_at=now(),
                    updated_at=now(),
                )
                project.busy = operation.operation_id
                self.s.store.operations[operation.operation_id] = (call.owner, operation)
                self.s.runner.start(lambda: self._run(project, file, operation))
                return OperationAccepted(
                    operation_id=operation.operation_id,
                    status_url=f"/api/v1/operations/{operation.operation_id}",
                )

        return await self.s.once(call, "import_geometry", request.model_dump(mode="json"), execute)

    async def _run(self, project: StoredProject, file: StoredFile, operation: OperationView):
        operation.status = "running"
        operation.updated_at = now()
        try:
            previous = project.view.geometry
            result = await self.s.provider.import_geometry(
                project.view.project_id,
                file.path,
                file.view.file_name,
                file.view.file_suffix,
                file.view.sha256,
                (previous.geometry_revision + 1) if previous else 1,
            )
            project.view.geometry = result
            # Mock re-import explicitly replaces geometry. Geometric bindings must be rebuilt.
            project.view.mesh = None
            project.view.properties = []
            project.view.case_settings = None
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
