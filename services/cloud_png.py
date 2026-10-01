"""Cloud PNG rendering for a finished run.

The render runs as its own process rather than inside the Worker, for two reasons that were both
observed on a real host:

* VTK needs a working OpenGL stack (Mesa's ``libGL``). When it is missing or broken the render can
  block or abort the interpreter - inside the Worker that kills the process, leaving the current
  run stuck at ``running`` forever and every later task unprocessed;
* a separate process can be killed when it hangs, so the run gets a failure instead of a status
  nobody can move away from.

The rendering itself belongs to the flow provider (``services/scripts/generate_cloud_png.py``),
which already accepts a ``cloud_info.json`` path on its command line.
"""

import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "scripts" / "generate_cloud_png.py"


def renderable_cloud_dir(cloud_info_path: Path) -> str | None:
    """The directory the cloud PNGs land in, or None when there is nothing to render.

    ``services/scripts/modal_test.py`` writes a cloud_info.json without a ``cloud_file_name``,
    which means "no VTK results to render"; that case must not spawn a renderer.
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
    return str(Path(first["cloud_file_name"]).parent)


def render_command(cloud_info_path: Path) -> list[str]:
    """The command that renders every cloud image described by cloud_info.json."""
    return [sys.executable, str(SCRIPT), str(cloud_info_path)]
