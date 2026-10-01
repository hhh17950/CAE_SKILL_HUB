"""The REST submission contract, from multipart upload to the task directory.

These tests run on any platform: they never import ``worker.py`` (which needs ``fcntl``).
Every case here is a regression that was observed in the real path.
"""

import json
import re
import sys
from pathlib import Path

import pytest

from core.config import Settings
from services import modal
from storage.repository import RunRepository
from tests.conftest import GEOMETRY, SUBMIT_URL, running_app, submit_modal

ROOT = Path(__file__).resolve().parents[1]
MODAL_SCRIPT = ROOT / "services" / "scripts" / "modal_nogui_process.py"


def script_environment_names() -> set[str]:
    """The GUIE_* names the real flow script reads, taken from its own environment spec."""
    source = MODAL_SCRIPT.read_text(encoding="utf-8")
    return set(re.findall(r'\(\s*"(GUIE_[A-Z_]+)"\s*,', source))


async def stored_parameters(settings, run_id: str) -> dict:
    repo = RunRepository(settings.database_path)
    try:
        row = await repo.get(run_id)
    finally:
        await repo.close()
    return json.loads(row["request_json"])


async def test_submission_creates_the_task_directory(client, settings, model_file):
    response = await submit_modal(client, model_file)

    assert response.status_code == 202, response.text
    run = response.json()
    assert run["status"] == "queued"
    assert response.headers["Location"] == run["status_url"] == f"/api/v1/guie-runs/{run['run_id']}"
    assert run["started_at"] is None and run["finished_at"] is None and run["exit_code"] is None
    assert set(run["log_urls"]) == {"stdout", "stderr", "jusmar"}

    task_dir = settings.workspace_root / run["run_id"]
    assert sorted(item.name for item in task_dir.iterdir()) == ["logs", "model", "project"]


async def test_uploaded_geometry_is_kept_byte_for_byte(client, settings, model_file):
    run = (await submit_modal(client, model_file)).json()

    # A text-mode handle would raise TypeError on bytes, or rewrite line endings.
    stored = settings.workspace_root / run["run_id"] / "model" / model_file[0]
    assert stored.read_bytes() == GEOMETRY


async def test_worker_parameters_use_the_names_the_worker_reads(client, settings, model_file):
    run = (await submit_modal(client, model_file)).json()
    parameters = await stored_parameters(settings, run["run_id"])

    assert parameters["poisson_ratio"] == 0.3
    assert "poisson_tatio" not in parameters
    assert parameters["model_filename"] == "part.stp"
    assert parameters["young_modulus"] == 2.0e11
    assert parameters["density"] == 7850
    assert parameters["number_of_roots"] == 10


async def test_environment_matches_the_real_flow_script(client, settings, model_file):
    run = (await submit_modal(client, model_file)).json()
    parameters = await stored_parameters(settings, run["run_id"])
    # The worker adds these three before building the environment.
    run_dir = settings.workspace_root / run["run_id"]
    parameters.update(run_dir=str(run_dir), test_sleep_seconds="0.01", test_exit_code="0")

    environment = modal.environment(parameters)

    missing = script_environment_names() - set(environment)
    assert not missing, f"{MODAL_SCRIPT.name} reads {sorted(missing)} but nothing sets them"
    assert environment["GUIE_POISSON_TATIO"] == "0.3"
    assert "GUIE_POISSON_TATID" not in environment
    assert Path(environment["GUIE_MODEL_PATH"]).read_bytes() == GEOMETRY
    assert environment["GUIE_CLOUD_INFO_DIR"] == str(run_dir / "cloud_info.json")


@pytest.mark.parametrize(
    "field,value",
    [
        ("poisson_ratio", "0.5"),
        ("poisson_ratio", "-1"),
        ("poisson_ratio", "1"),
        ("poisson_ratio", "abc"),
        ("young_modulus", "0"),
        ("young_modulus", "-1"),
        ("density", "0"),
        ("density", "1.5"),
        ("density", "true"),
        ("number_of_roots", "0"),
    ],
)
async def test_rejects_values_outside_the_documented_ranges(client, model_file, field, value):
    response = await submit_modal(client, model_file, **{field: value})
    assert response.status_code == 422, response.text
    assert response.headers["content-type"] == "application/problem+json"


async def test_rejects_missing_or_empty_model(client):
    empty = await submit_modal(client, model=("part.stp", b"", "application/octet-stream"))
    assert empty.status_code == 422 and empty.json()["code"] == "MODEL_EMPTY"
    assert (await submit_modal(client, model=None)).status_code == 422


async def test_rejects_model_over_the_size_limit(settings):
    config = settings.model_copy(update={"max_model_bytes": 8})
    async with running_app(config) as client:
        response = await submit_modal(client)
        assert response.status_code == 413
        assert response.json()["code"] == "MODEL_TOO_LARGE"


async def test_status_results_logs_and_cloud_before_the_worker_runs(client, model_file):
    run = (await submit_modal(client, model_file)).json()

    assert (await client.get(run["status_url"])).json()["status"] == "queued"
    unready = await client.get(run["result_url"])
    assert unready.status_code == 409 and unready.json()["code"] == "RESULT_NOT_READY"
    for url in run["log_urls"].values():
        assert (await client.get(url)).status_code == 404
    invalid_kind = await client.get(f"/api/v1/guie-runs/{run['run_id']}/logs/other")
    assert invalid_kind.status_code == 422
    unknown = await client.get("/api/v1/guie-runs/run_missing")
    assert unknown.status_code == 404
    cloud = await client.get(f"/api/v1/guie-runs/{run['run_id']}/cloud/cloud_3d_1.png")
    assert cloud.status_code == 404


async def test_optional_fields_fall_back_to_the_documented_defaults(client, settings, model_file):
    response = await client.post(SUBMIT_URL, files={"model_file": model_file})

    assert response.status_code == 202, response.text
    parameters = await stored_parameters(settings, response.json()["run_id"])
    assert parameters["young_modulus"] == 2.0e11
    assert parameters["poisson_ratio"] == 0.3
    assert parameters["density"] == 7850
    assert parameters["number_of_roots"] == 10


async def test_commands_point_at_the_configured_launcher(settings):
    without_launcher = modal.command(settings)
    assert without_launcher[0] == sys.executable
    assert Path(without_launcher[1]).name == "modal_test.py"

    with_launcher = modal.command(
        settings.model_copy(update={"guierunner_path": Path("/opt/guierunner")})
    )
    # Compare via Path so the assertion is agnostic to the host separator
    # (str(Path("/opt/guierunner")) is "\\opt\\guierunner" on Windows).
    assert Path(with_launcher[0]) == Path("/opt/guierunner")
    assert with_launcher[1] == "nogui"
    assert Path(with_launcher[2]).name == "modal_nogui_process.py"


def test_blank_launcher_is_treated_as_unset():
    # Compose passes CAE_GUIERUNNER_PATH="" when the host variable is unset; Path("") would
    # otherwise become "." and the worker would try to execute the working directory.
    assert Settings(_env_file=None, guierunner_path="").guierunner_path is None
    assert Settings(_env_file=None).guierunner_path is None
