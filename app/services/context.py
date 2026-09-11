import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from app.config import Settings
from app.errors import DomainError, unsupported
from app.execution.runner import Runner
from app.providers.interface import CaeProvider
from app.schemas.common import Parameters
from app.storage.memory import MemoryStore, StoredProject

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class CallContext:
    owner: str
    idempotency_key: str


@dataclass
class IdempotentCall:
    fingerprint: str
    task: asyncio.Task


class Services:
    """Shared execution mechanics; business dispatch remains explicit in each service."""

    def __init__(
        self, settings: Settings, provider: CaeProvider, store: MemoryStore, runner: Runner
    ):
        self.settings = settings
        self.provider = provider
        self.store = store
        self.runner = runner
        self.idempotency: dict[tuple[str, str, str], IdempotentCall] = {}

    def validate_extensions(self, request: Parameters):
        if request.extensions.jusmar_app:
            raise unsupported("extensions.jusmar_app", "当前未注册 SDK 扩展参数")

    async def once(
        self,
        call: CallContext,
        scope: str,
        payload: dict,
        action: Callable[[], Awaitable[T]],
    ) -> T:
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        ).hexdigest()
        key = (call.owner, scope, call.idempotency_key)
        entry = self.idempotency.get(key)
        if entry:
            if entry.fingerprint != fingerprint:
                raise DomainError("IDEMPOTENCY_KEY_REUSED", "该幂等键已用于不同请求", 409)
        else:
            if len(self.idempotency) >= self.settings.max_records:
                raise DomainError("CAPACITY_EXCEEDED", "幂等记录上限已达到", 429)
            # No await between checking and inserting: atomic within this one event loop.
            entry = IdempotentCall(fingerprint, self.runner.start(action))
            self.idempotency[key] = entry
        try:
            result = await asyncio.shield(entry.task)
        except asyncio.CancelledError:
            if entry.task.cancelled():
                raise DomainError("EXECUTION_UNKNOWN", "执行已中断，禁止盲目重试", 503) from None
            raise
        return result.model_copy(deep=True)

    async def mutate(
        self,
        call: CallContext,
        project_id: str,
        name: str,
        request: Parameters,
        action: Callable[[StoredProject], Awaitable[T]],
    ) -> T:
        self.validate_extensions(request)
        project = self.store.project(call.owner, project_id)

        async def execute():
            async with project.lock:
                self.store.require_idle(project)
                return await action(project)

        return await self.once(
            call, f"{name}:{project_id}", request.model_dump(mode="json"), execute
        )
