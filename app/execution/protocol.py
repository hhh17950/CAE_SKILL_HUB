"""Versioned file boundary. GUI-specific command-line syntax is not a public API."""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.analysis import ModalAnalysisRequest, RunResult, StepView
from app.schemas.common import ApiModel, ExecutionError

MAX_MANIFEST_BYTES = 1024 * 1024


class JobInput(ApiModel):
    protocol_version: Literal["1.0"] = "1.0"
    execution_mode: Literal["mock"] = "mock"
    run_id: str
    attempt_id: str
    implementation_version: str
    request: ModalAnalysisRequest
    geometry_path: Literal["geometry.input"] = "geometry.input"
    geometry_suffix: str
    geometry_sha256: str
    mock_delay_seconds: float = Field(ge=0, le=30)
    # Test-only constructor setting, never accepted by the public analysis schema.
    mock_failures: list[str] = Field(default_factory=list)


class JobProgress(ApiModel):
    protocol_version: Literal["1.0"] = "1.0"
    run_id: str
    attempt_id: str
    steps: list[StepView]


class JobOutput(JobProgress):
    implementation_version: str
    status: Literal["succeeded", "failed"]
    runtime_versions: dict[str, str]
    result: RunResult | None = None
    error: ExecutionError | None = None

    @model_validator(mode="after")
    def result_matches_status(self):
        if self.status == "succeeded" and (self.result is None or self.error is not None):
            raise ValueError("successful output requires a result and no error")
        if self.status == "failed" and (self.error is None or self.result is not None):
            raise ValueError("failed output requires an error and no result")
        return self


def atomic_json(path: Path, value: ApiModel | dict):
    """Atomic rename avoids partial manifests; fsync the content before publication."""
    body = value.model_dump(mode="json") if isinstance(value, ApiModel) else value
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(body, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def read_manifest(path: Path) -> dict:
    with path.open("rb") as source:
        data = source.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest exceeds size limit")
    return json.loads(data)
