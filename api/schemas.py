"""Shared API response models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)


class ErrorItem(ApiModel):
    field: str
    reason: str


class Problem(ApiModel):
    type: str = "about:blank"
    title: str
    status: int
    instance: str
    code: str
    detail: str
    request_id: str
    retryable: bool = False
    errors: list[ErrorItem] = Field(default_factory=list)


class GuieRunView(ApiModel):
    run_id: str
    status: Literal["queued", "running", "succeeded", "failed", "timed_out", "unknown"]
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    error: str | None = None
    status_url: str
    result_url: str
    log_urls: dict[str, str]
