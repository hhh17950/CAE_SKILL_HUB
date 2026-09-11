from app.errors import DomainError
from app.schemas.common import SettingsResult
from app.schemas.post_processing import PostProcessingRequest
from app.services.context import CallContext, Services
from app.storage.memory import StoredProject


class PostProcessingService:
    def __init__(self, services: Services):
        self.s = services

    async def post_processing_settings(
        self, call: CallContext, project_id: str, request: PostProcessingRequest
    ):
        if len(set(request.output_items)) != len(request.output_items):
            raise DomainError("INVALID_PARAMETER", "output_items 不允许重复", 422)

        async def execute(project: StoredProject):
            await self.s.provider.post_processing_settings(project_id, request)
            project.view.post_processing_settings = request.model_copy(deep=True)
            project.view.revision += 1
            return SettingsResult(project_id=project_id, revision=project.view.revision)

        return await self.s.mutate(call, project_id, "post_processing_settings", request, execute)
