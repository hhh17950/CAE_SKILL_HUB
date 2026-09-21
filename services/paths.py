"""Validate caller-selected filesystem paths shared by API and Worker."""

from pathlib import Path

from core.config import Settings


def existing_project_dir(value: str, settings: Settings) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("project_dir 必须为服务端绝对路径")
    resolved = path.resolve()
    if not resolved.is_relative_to(settings.project_root.resolve()) or not resolved.is_dir():
        raise ValueError("project_dir 不存在或不在允许的工程根目录")
    return resolved


def new_output_file(value: str, settings: Settings, field: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{field} 必须为服务端绝对路径")
    resolved = path.resolve()
    root = settings.output_root.resolve()
    if not resolved.is_relative_to(root) or not resolved.parent.is_dir():
        raise ValueError(f"{field} 的父目录不存在或超出允许的输出根目录")
    if resolved.exists():
        raise ValueError(f"{field} 已存在，请为新任务指定新文件")
    if field == "cloud_info_path" and resolved.suffix.lower() != ".json":
        raise ValueError("cloud_info_path 必须指向 .json 文件")
    return resolved
