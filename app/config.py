from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAE_", env_file=".env", extra="ignore")

    provider: Literal["mock", "jusmar"] = "mock"
    api_tokens: dict[str, str] = Field(default_factory=lambda: {"local-test-token": "demo"})
    upload_root: Path = Path(".runtime/uploads")
    allow_server_paths: bool = False
    server_path_root: Path | None = None
    max_file_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    max_records: int = Field(default=10000, ge=10)
    max_process_num: int = Field(default=8, ge=1)
    mock_delay_seconds: float = Field(default=0.05, ge=0, le=30)
    database_path: Path = Path(".runtime/cae.db")
    asset_root: Path = Path(".runtime/assets")
    job_root: Path = Path(".runtime/jobs")
    enable_legacy_api: bool = False
    worker_poll_seconds: float = Field(default=0.2, ge=0.02, le=30)
    worker_lease_seconds: float = Field(default=15, ge=2, le=300)
    run_timeout_seconds: float = Field(default=300, ge=1, le=86400)

    @model_validator(mode="after")
    def validate_settings(self):
        if not self.api_tokens or any(
            not token or not owner for token, owner in self.api_tokens.items()
        ):
            raise ValueError("api_tokens must map non-empty tokens to owners")
        if self.allow_server_paths and self.server_path_root is None:
            raise ValueError("server_path_root is required when server paths are enabled")
        return self
