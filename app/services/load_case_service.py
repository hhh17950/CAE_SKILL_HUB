from app.schemas.load_case import AddLoadCaseRequest
from app.services.context import CallContext, Services
from app.storage.memory import StoredProject


class LoadCaseService:
    def __init__(self, services: Services):
        self.s = services

    async def add_load_case(self, call: CallContext, project_id: str, request: AddLoadCaseRequest):
        async def execute(project: StoredProject):
            result = await self.s.provider.add_load_case(project_id, request)
            project.view.load_cases.append(result)
            project.view.revision += 1
            return result

        return await self.s.mutate(call, project_id, "add_load_case", request, execute)
