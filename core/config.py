"""Single-flow service settings."""

from pathlib import Path

from pydantic import Field, field_validator
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
    # 云图渲染单独设超时：它在独立进程里跑（VTK 需要可用的 OpenGL/Mesa，缺库时会卡住或直接崩溃），
    # 没有这一步的保护，任务会永远停在 running。与 run_timeout_seconds 一样可配。
    cloud_timeout_seconds: float = Field(default=300, ge=1, le=86400)
    max_request_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    max_model_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    mcp_enabled: bool = True
    # 用于把服务器本地图片路径转成智能体可下载的 URL，必须是**调用方能访问到**的地址：部署时填
    # 局域网 IP 或域名（如 http://192.168.16.128:8000）。0.0.0.0 是监听地址而不是可访问地址，
    # 填了只会得到打不开的 image_url；这里默认 127.0.0.1 只够本机自测。
    public_base_url: str = "http://127.0.0.1:8000"

    @field_validator("guierunner_path", mode="before")
    @classmethod
    def _blank_guierunner_is_unset(cls, value):
        """Compose passes an empty CAE_GUIERUNNER_PATH when the host variable is unset."""
        if isinstance(value, str) and not value.strip():
            return None
        return value
