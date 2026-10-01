"""Cloud rendering contract: what is renderable, and which process renders it.

Rendering deliberately happens in its own process (VTK needs Mesa's libGL; a crash or a hang there
must not take the Worker down or leave a run stuck at ``running``). The Worker behaviour itself is
covered in ``test_runs.py``, which is POSIX-only; the checks here run everywhere.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from services import cloud_png

ROOT = Path(__file__).resolve().parents[1]


def write_cloud_info(root: Path, entries) -> Path:
    path = root / "cloud_info.json"
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return path


def test_missing_cloud_info_renders_nothing(tmp_path):
    assert cloud_png.renderable_cloud_dir(tmp_path / "cloud_info.json") is None


def test_cloud_info_without_a_png_target_renders_nothing(tmp_path):
    """``services/scripts/modal_test.py`` writes a cloud_info.json with no PNG to render."""
    assert cloud_png.renderable_cloud_dir(write_cloud_info(tmp_path, {})) is None
    assert (
        cloud_png.renderable_cloud_dir(
            write_cloud_info(tmp_path, {"test_only": True, "model_name": "part.stp"})
        )
        is None
    )


def test_cloud_dir_is_the_parent_of_the_declared_png(tmp_path):
    cloud_dir = tmp_path / "project" / "cloud_png"
    path = write_cloud_info(
        tmp_path,
        {
            "1": {
                "vtk_file": str(tmp_path / "project" / "mode_1.vtk"),
                "cloud_file_name": str(cloud_dir / "cloud_3d_1.png"),
                "frequency": 12.5,
            }
        },
    )
    assert cloud_png.renderable_cloud_dir(path) == str(cloud_dir)


def test_render_command_runs_the_provider_script_on_the_json(tmp_path):
    """The renderer is the flow provider's own script, spawned with this interpreter."""
    path = tmp_path / "cloud_info.json"
    command = cloud_png.render_command(path)
    assert command[0] == sys.executable
    assert Path(command[1]) == Path(cloud_png.SCRIPT)
    assert Path(command[1]).is_file(), "the renderer script must ship with the service"
    assert command[2] == str(path)


def test_importing_the_renderer_module_never_pulls_vtk_into_this_process():
    """``import vtk`` blocks forever without a usable OpenGL stack, so it stays out of the Worker.

    This was measured, not assumed: on a host with no working libGL the interpreter never finished
    the import. Doing that inside the Worker would freeze the Worker itself with the run left at
    ``running`` - the exact failure the separate renderer process exists to prevent. A child
    interpreter is used (with a timeout) so a regression fails the test instead of hanging the run.
    """
    code = "import sys; import services.cloud_png; print('vtk' in sys.modules)"
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise AssertionError(
            "importing services.cloud_png did not finish in 60s: VTK is being imported in-process"
        ) from None
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", result.stdout
