"""Single-flow service settings."""

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAE_", env_file=".env", extra="ignore")

    api_tokens: dict[str, str] = Field(default_factory=lambda: {"local-test-token": "demo"})
    model_root: Path | None = None
    project_root: Path = Path("workspace")
    output_root: Path = Path("workspace")
    database_path: Path = Path("workspace/guie_ori.db")
    run_root: Path = Path("workspace/runs")
    task_log_root: Path = Path("static/logs")
    service_log_root: Path = Path("workspace/logs")
    test_sleep_seconds: float = Field(default=60, ge=0, le=3600)
    test_exit_code: int = Field(default=0, ge=0, le=255)
    worker_poll_seconds: float = Field(default=0.2, ge=0.02, le=30)
    run_timeout_seconds: float = Field(default=300, ge=1, le=86400)
    max_request_bytes: int = Field(default=65536, ge=1024)

    @model_validator(mode="after")
    def validate_tokens(self):
        if not self.api_tokens or any(
            not token or not owner for token, owner in self.api_tokens.items()
        ):
            raise ValueError("api_tokens must map non-empty tokens to owners")
        return self
