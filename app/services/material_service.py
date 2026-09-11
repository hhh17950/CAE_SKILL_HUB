from app.schemas.material import CreateMaterialRequest
from app.services.context import CallContext, Services
from app.storage.memory import StoredProject


class MaterialService:
    def __init__(self, services: Services):
        self.s = services

    async def create_material(
        self, call: CallContext, project_id: str, request: CreateMaterialRequest
    ):
        async def execute(project: StoredProject):
            result = await self.s.provider.create_material(project_id, request)
            project.view.materials.append(result)
            project.view.revision += 1
            return result

        return await self.s.mutate(call, project_id, "create_material", request, execute)
