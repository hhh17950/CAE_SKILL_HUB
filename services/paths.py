"""Validate caller-selected filesystem paths shared by API and Worker."""

from pathlib import Path

from core.config import Settings


def task_dir(settings: Settings, run_id: str) -> Path:
    return settings.workspace_root.resolve() / run_id


def create_task_dir(settings: Settings, run_id: str) -> Path:
    root = task_dir(settings, run_id)
    root.mkdir(parents=True, exist_ok=False)
    (root / "model").mkdir()
    (root / "project").mkdir()
    (root / "logs").mkdir()
    return root


def model_path(root: Path, filename: str) -> Path:
    return root / "model" / filename


def project_dir(root: Path) -> Path:
    return root / "project"


def jusmar_log(root: Path) -> Path:
    return root / "jusmar.log"


def cloud_info(root: Path) -> Path:
    return root / "cloud_info.json"


def stdout_log(root: Path) -> Path:
    return root / "logs" / "stdout.log"


def stderr_log(root: Path) -> Path:
    return root / "logs" / "stderr.log"
