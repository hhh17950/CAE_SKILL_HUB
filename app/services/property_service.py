from app.errors import DomainError, unsupported
from app.schemas.property import CreatePropertyRequest
from app.services.context import CallContext, Services
from app.storage.memory import StoredProject


class PropertyService:
    def __init__(self, services: Services):
        self.s = services

    async def create_3d_property(
        self, call: CallContext, project_id: str, request: CreatePropertyRequest
    ):
        if request.geometry_ref_enable:
            raise unsupported("geometry_ref_enable")

        async def execute(project: StoredProject):
            if request.material_ref not in {m.material_id for m in project.view.materials}:
                raise DomainError("RESOURCE_REFERENCE_CONFLICT", "材料不存在或不属于当前工程", 409)
            result = await self.s.provider.create_3d_property(project_id, request)
            project.view.properties.append(result)
            project.view.revision += 1
            return result

        return await self.s.mutate(call, project_id, "create_3d_property", request, execute)
