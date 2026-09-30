"""Single-flow service settings."""

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAE_", env_file=".env", extra="ignore")

    workspace_root: Path = Path("workspace")
    guierunner_path: Path | None = None
    database_path: Path = Path("workspace/guie.db")
    service_log_root: Path = Path("workspace/logs")
    test_sleep_seconds: float = Field(default=60, ge=0, le=3600)
    test_exit_code: int = Field(default=0, ge=0, le=255)
    worker_poll_seconds: float = Field(default=0.2, ge=0.02, le=30)
    run_timeout_seconds: float = Field(default=300, ge=1, le=86400)
    max_request_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    max_model_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    mcp_enabled: bool = True
    # 用于把服务器本地图片路径转成智能体可下载的 URL。
    public_base_url: str = "http://0.0.0.0:8000"
