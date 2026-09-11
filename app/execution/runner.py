import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.errors import DomainError
from app.schemas.common import ExecutionError

logger = logging.getLogger(__name__)
T = TypeVar("T")


def execution_error(exc: Exception) -> ExecutionError:
    if isinstance(exc, DomainError):
        return ExecutionError(
            code=exc.code, detail=exc.detail, retryable=exc.retryable, errors=exc.field_errors
        )
    logger.error("Unexpected mock execution error", exc_info=exc)
    return ExecutionError(code="INTERNAL_ERROR", detail="执行异常，请联系服务维护人员")


class Runner:
    """Lifespan-owned tasks; requests may disconnect without cancelling accepted work."""

    def __init__(self):
        self.tasks: set[asyncio.Task] = set()
        self.closing = False

    def start(self, factory: Callable[[], Awaitable[T]]) -> asyncio.Task[T]:
        if self.closing:
            raise DomainError("SERVICE_STOPPING", "服务正在关闭", 503)
        task = asyncio.create_task(factory())
        self.tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task):
        self.tasks.discard(task)
        if not task.cancelled():
            task.exception()  # Observe failures even if the HTTP caller disconnected.

    async def close(self):
        self.closing = True
        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
