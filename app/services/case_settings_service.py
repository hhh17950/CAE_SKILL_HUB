from app.errors import DomainError, unsupported
from app.schemas.case_settings import CaseSettingsRequest
from app.schemas.common import SettingsResult
from app.services.context import CallContext, Services
from app.storage.memory import StoredProject


class CaseSettingsService:
    def __init__(self, services: Services):
        self.s = services

    async def case_settings(self, call: CallContext, project_id: str, request: CaseSettingsRequest):
        if request.pre_stress_temp:
            raise unsupported("pre_stress_temp")
        if request.eigen_lower_bound:
            raise unsupported("eigen_lower_bound")
        if request.eigen_upper_bound:
            raise unsupported("eigen_upper_bound")
        if not request.eigen_count_enable:
            raise unsupported("eigen_count_enable")

        async def execute(project: StoredProject):
            if request.selected_load_case not in {c.load_case_id for c in project.view.load_cases}:
                raise DomainError("RESOURCE_REFERENCE_CONFLICT", "工况不存在或不属于当前工程", 409)
            await self.s.provider.case_settings(project_id, request)
            project.view.case_settings = request.model_copy(deep=True)
            project.view.revision += 1
            return SettingsResult(project_id=project_id, revision=project.view.revision)

        return await self.s.mutate(call, project_id, "case_settings", request, execute)
