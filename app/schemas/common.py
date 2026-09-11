from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=160)]
Name = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200, pattern=r"\S")]
Status = Literal["queued", "running", "succeeded", "failed", "unknown", "reconciling", "cancelled"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)


class Extensions(ApiModel):
    jusmar_app: dict[str, JsonValue] = Field(default_factory=dict)


class Parameters(ApiModel):
    extensions: Extensions = Field(default_factory=Extensions)


class ErrorItem(ApiModel):
    field: str
    reason: str


class ExecutionError(ApiModel):
    code: str
    detail: str
    retryable: bool = False
    errors: list[ErrorItem] = Field(default_factory=list)


class Problem(ExecutionError):
    type: str = "about:blank"
    title: str
    status: int
    instance: str
    request_id: str


class Response[T](ApiModel):
    request_id: str
    execution_mode: Literal["mock"] = "mock"
    data: T
    warnings: list[str] = Field(default_factory=list)


class SettingsResult(ApiModel):
    project_id: str
    revision: int


class OperationAccepted(ApiModel):
    operation_id: str
    status: Status = "queued"
    status_url: str


class TaskAccepted(ApiModel):
    task_id: str
    status: Status = "queued"
    status_url: str
