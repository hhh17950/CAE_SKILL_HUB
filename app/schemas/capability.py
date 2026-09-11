from typing import Literal

from pydantic import Field, JsonValue

from app.schemas.common import ApiModel


class Capabilities(ApiModel):
    contract_version: str = "0.1.0-draft"
    execution_mode: Literal["mock"] = "mock"
    storage_mode: Literal["memory"] = "memory"
    restart_recovery: bool = False
    physical_results: bool = False
    operations: list[str]
    geometry_formats: list[str]
    limits: dict[str, int]
    supported_parameters: dict[str, JsonValue]
    notes: list[str] = Field(default_factory=list)
