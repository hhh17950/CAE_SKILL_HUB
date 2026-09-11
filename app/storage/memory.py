"""Single-event-loop state. Not durable and not shared between worker processes."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.errors import DomainError, not_found
from app.schemas.file import FileResult
from app.schemas.operation import OperationView, TaskView
from app.schemas.project import ProjectView


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class StoredFile:
    owner: str
    path: Path
    view: FileResult


@dataclass
class StoredProject:
    owner: str
    view: ProjectView
    lock: asyncio.Lock
    busy: str | None = None


class MemoryStore:
    def __init__(self, max_records: int):
        self.max_records = max_records
        self.projects: dict[str, StoredProject] = {}
        self.files: dict[str, StoredFile] = {}
        self.operations: dict[str, tuple[str, OperationView]] = {}
        self.tasks: dict[str, tuple[str, TaskView]] = {}

    def check_capacity(self):
        count = len(self.projects) + len(self.files) + len(self.operations) + len(self.tasks)
        if count >= self.max_records:
            raise DomainError("CAPACITY_EXCEEDED", "演示服务记录上限已达到", 429)

    def project(self, owner: str, project_id: str) -> StoredProject:
        project = self.projects.get(project_id)
        if project is None or project.owner != owner:
            raise not_found("工程")
        return project

    def file(self, owner: str, file_id: str) -> StoredFile:
        file = self.files.get(file_id)
        if file is None or file.owner != owner:
            raise not_found("文件")
        return file

    def operation(self, owner: str, operation_id: str) -> OperationView:
        item = self.operations.get(operation_id)
        if item is None or item[0] != owner:
            raise not_found("操作")
        return item[1].model_copy(deep=True)

    def task(self, owner: str, task_id: str) -> TaskView:
        item = self.tasks.get(task_id)
        if item is None or item[0] != owner:
            raise not_found("任务")
        return item[1].model_copy(deep=True)

    def require_idle(self, project: StoredProject):
        if project.busy:
            raise DomainError("PROJECT_BUSY", f"工程正在执行 {project.busy}，请先查询状态", 409)
