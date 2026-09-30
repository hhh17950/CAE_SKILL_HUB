import json
from pathlib import Path

from services.scripts.generate_cloud_png import generate_cloud_png


def render_cloud_from_json(cloud_info_path: Path) -> str | None:
    """Generate cloud PNG from a task's cloud_info.json and return the cloud directory
    (the parent of the first generated ``cloud_file_name``), or None when cloud_info.json is
    absent or has no renderable entries.
    """
    cloud_info_path = Path(cloud_info_path)
    if not cloud_info_path.is_file():
        return None
    with open(cloud_info_path, encoding="utf-8") as f:
        cloud_info = json.load(f)
    if not isinstance(cloud_info, dict) or not cloud_info:
        return None
    first = next(iter(cloud_info.values()))
    if not isinstance(first, dict) or not first.get("cloud_file_name"):
        return None
    cloud_dir = str(Path(first["cloud_file_name"]).parent)
    for entry in cloud_info.values():
        generate_cloud_png(
            vtk_file=entry["vtk_file"],
            cloud_file_name=entry["cloud_file_name"],
            frequency=entry["frequency"],
        )
    return cloud_dir
