from app.errors import unsupported
from app.schemas.common import SettingsResult
from app.schemas.solver import SolverSettingsRequest
from app.services.context import CallContext, Services
from app.storage.memory import StoredProject


class SolverService:
    def __init__(self, services: Services):
        self.s = services

    async def solver_settings(
        self, call: CallContext, project_id: str, request: SolverSettingsRequest
    ):
        if request.solver_type != "mock_modal":
            raise unsupported("solver_type", "本模拟配置仅接受 mock_modal")
        if request.linear_solver_type != "mock_direct":
            raise unsupported("linear_solver_type", "本模拟配置仅接受 mock_direct")

        async def execute(project: StoredProject):
            await self.s.provider.solver_settings(project_id, request)
            project.view.solver_settings = request.model_copy(deep=True)
            project.view.revision += 1
            return SettingsResult(project_id=project_id, revision=project.view.revision)

        return await self.s.mutate(call, project_id, "solver_settings", request, execute)
