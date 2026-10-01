"""End-to-end runs: multipart submission -> real Worker -> results, logs and the CLI.

``worker.py`` imports ``fcntl`` and ``services/executor.py`` calls ``os.killpg``, so every case
that actually executes a script is POSIX-only and skipped on Windows. Cases that only exercise
the API, the repository or the Alembic CLI still run everywhere.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import text

from core.config import Settings
from main import create_app
from storage.repository import RunRepository
from tests.conftest import migrate, running_app, submit_modal

BASE = "/api/v1/guie-runs"

# Executing a task spawns a real process group, which only exists on POSIX.
needs_posix_worker = pytest.mark.skipif(
    sys.platform == "win32",
    reason="worker.py 依赖 fcntl、services/executor.py 依赖 os.killpg，只能在 Linux 目标平台运行",
)


def load_worker():
    """Import the POSIX-only worker module lazily, so this file still collects on Windows."""
    import worker as worker_module

    return worker_module


async def submit(client, **fields):
    response = await submit_modal(client, **fields)
    assert response.status_code == 202, response.text
    run = response.json()
    assert response.headers["Location"] == run["status_url"]
    return run


@needs_posix_worker
async def test_success_writes_outputs_and_logs(client, settings, model_file):
    worker = load_worker()
    run = await submit(client, model=model_file)
    assert run["status"] == "queued"
    assert (await client.get(run["result_url"])).status_code == 409

    assert await worker.GuieWorker(settings).run_once()

    state = (await client.get(run["status_url"])).json()
    assert state["status"] == "succeeded" and state["exit_code"] == 0
    cloud = (await client.get(run["result_url"])).json()["cloud_info"]
    assert cloud["test_only"] is True
    assert cloud["model_name"] == "part.stp"
    assert cloud["parameters"] == {
        "young_modulus": 2.0e11,
        "poisson_ratio": 0.3,
        "density": 7850,
        "number_of_roots": 10,
    }
    for kind, phrase in (
        ("stdout", "test completed"),
        ("stderr", "capture ready"),
        ("jusmar", "test completed"),
    ):
        log = await client.get(run["log_urls"][kind])
        assert log.status_code == 200 and phrase in log.text
    assert list(settings.service_log_root.glob("api-*.log"))


@needs_posix_worker
async def test_each_submission_is_a_new_task(client, settings, model_file):
    worker = load_worker()
    first = await submit(client, model=model_file)
    second = await submit(client, model=model_file)
    assert first["run_id"] != second["run_id"]

    instance = worker.GuieWorker(settings)
    assert await instance.run_once()
    assert await instance.run_once()
    assert not await instance.run_once()  # The queue is empty again.

    for run in (first, second):
        task_dir = settings.workspace_root / run["run_id"]
        assert (task_dir / "model" / "part.stp").is_file()
        assert (task_dir / "logs" / "stdout.log").is_file()
        assert (task_dir / "logs" / "stderr.log").is_file()
        assert (task_dir / "jusmar.log").is_file()
        assert (task_dir / "cloud_info.json").is_file()


async def test_no_authentication_layer(client, model_file):
    """Access control belongs to the deployment layer; the service never rejects a token."""
    run = (await submit_modal(client, model_file)).json()
    for headers in ({}, {"Authorization": "Bearer garbage"}, {"Authorization": ""}):
        assert (await client.get(run["status_url"], headers=headers)).status_code == 200
    # An unknown run is 404 because it does not exist, not because of a missing credential.
    assert (await client.get(f"{BASE}/run_missing")).status_code == 404
    assert (
        await client.post(
            f"{BASE}/modal",
            data={"young_modulus": "2.0e11", "poisson_ratio": "0.3", "density": "7850"},
            files={"model_file": ("part.stp", b"x", "application/octet-stream")},
            headers={"Authorization": "Bearer garbage"},
        )
    ).status_code == 202


async def test_request_body_limit(settings, model_file):
    config = settings.model_copy(update={"max_request_bytes": 4096})
    async with running_app(config) as client:
        oversized = ("part.stp", b"x" * 8192, "application/octet-stream")
        response = await submit_modal(client, oversized)
        assert response.status_code == 413


@needs_posix_worker
async def test_failure_and_timeout(client, settings, model_file):
    worker = load_worker()
    for update, status, code in (
        ({"test_exit_code": 7}, "failed", 7),
        ({"test_sleep_seconds": 60, "run_timeout_seconds": 1}, "timed_out", -9),
    ):
        run = await submit(client, model=model_file)
        await worker.GuieWorker(settings.model_copy(update=update)).run_once()
        result = (await client.get(run["status_url"])).json()
        assert result["status"] == status and result["exit_code"] == code
        assert (await client.get(run["result_url"])).status_code == 409


@needs_posix_worker
async def test_unstartable_launcher_names_the_reason(client, settings, model_file, tmp_path):
    """A command that never starts leaves no output, so the reason must reach the caller."""
    worker = load_worker()
    run = await submit(client, model=model_file)
    missing = tmp_path / "no-such-launcher"
    await worker.GuieWorker(settings.model_copy(update={"guierunner_path": missing})).run_once()

    state = (await client.get(run["status_url"])).json()
    assert state["status"] == "failed" and state["exit_code"] is None
    assert str(missing) in state["error"]

    log = await client.get(f"{BASE}/{run['run_id']}/logs/stderr")
    assert log.status_code == 200
    assert str(missing) in log.text


@needs_posix_worker
async def test_cancel_marks_unknown(client, settings, model_file):
    worker = load_worker()
    run = await submit(client, model=model_file)
    task = asyncio.create_task(
        worker.GuieWorker(settings.model_copy(update={"test_sleep_seconds": 60})).run_once()
    )
    log = settings.workspace_root / run["run_id"] / "logs" / "stdout.log"
    for _ in range(200):
        if log.is_file() and "test completed" in log.read_text(encoding="utf-8", errors="replace"):
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("script did not start")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await client.get(run["status_url"])).json()["status"] == "unknown"


@needs_posix_worker
async def test_cloud_render_timeout_fails_the_run(client, settings, model_file, monkeypatch):
    """A cloud render that hangs must end as ``failed``, never leave the run ``running`` forever.

    Rendering runs as its own process because it needs Mesa's libGL. Before that, a VTK crash killed
    the Worker with the run still marked running, and a plain hang was equally unrecoverable; the
    configurable timeout is what turns either case into a status the caller can act on.
    """
    from services import cloud_png

    worker = load_worker()
    run = await submit(client, model=model_file)
    run_dir = settings.workspace_root.resolve() / run["run_id"]
    cloud_dir = run_dir / "project" / "cloud_png"
    (run_dir / "cloud_info.json").write_text(
        json.dumps(
            {
                "1": {
                    "vtk_file": str(run_dir / "project" / "mode_1.vtk"),
                    "cloud_file_name": str(cloud_dir / "cloud_3d_1.png"),
                    "frequency": 12.5,
                }
            }
        ),
        encoding="utf-8",
    )
    # A renderer that never returns: the timeout has to kill it and report the run as failed.
    monkeypatch.setattr(
        cloud_png,
        "render_command",
        lambda _path: [sys.executable, "-c", "import time; time.sleep(60)"],
    )

    config = settings.model_copy(update={"cloud_timeout_seconds": 1})
    assert await worker.GuieWorker(config).run_once()

    state = (await client.get(run["status_url"])).json()
    assert state["status"] == "failed"
    assert "超时" in (state["error"] or "")


@needs_posix_worker
async def test_result_missing_or_invalid(client, settings, model_file):
    worker = load_worker()
    run = await submit(client, model=model_file)
    await worker.GuieWorker(settings).run_once()

    result = settings.workspace_root / run["run_id"] / "cloud_info.json"
    result.write_text("invalid json", encoding="utf-8")
    assert (await client.get(run["result_url"])).status_code == 500
    result.unlink()
    assert (await client.get(run["result_url"])).status_code == 404


@needs_posix_worker
async def test_cloud_image_must_be_declared_by_cloud_info(client, settings, model_file):
    worker = load_worker()
    run = await submit(client, model=model_file)
    await worker.GuieWorker(settings).run_once()

    # The example flow declares no renderable cloud file, so nothing can be downloaded yet.
    cloud = await client.get(f"{BASE}/{run['run_id']}/cloud/cloud_3d_1.png")
    assert cloud.status_code == 404
    traversal = await client.get(f"{BASE}/{run['run_id']}/cloud/..%2Fcloud_info.json")
    assert traversal.status_code in (404, 422)


async def test_simulated_run_serves_results_logs_and_images(client, settings):
    """The demo helper must expose the same caller surface as a real finished run.

    Unlike the cases above this needs no Worker and no POSIX, so it also guards the cloud-image
    path on the Windows development host.
    """
    from services.scripts.simulate_cloud_run import simulate

    row = await simulate(settings, "run_demo_rest", modes=2)
    assert row["status"] == "succeeded" and row["exit_code"] == 0

    state = (await client.get(f"{BASE}/run_demo_rest")).json()
    assert state["status"] == "succeeded" and state["error"] is None

    results = (await client.get(f"{BASE}/run_demo_rest/results")).json()
    assert results["exit_code"] == 0
    assert sorted(results["cloud_info"]) == ["1", "2"]
    assert Path(results["cloud_dir"]).is_dir()
    for entry in results["cloud_info"].values():
        name = Path(entry["cloud_file_name"]).name
        image = await client.get(f"{BASE}/run_demo_rest/cloud/{name}")
        assert image.status_code == 200
        assert image.content.startswith(b"\x89PNG\r\n\x1a\n")

    assert "模拟数据" in (await client.get(f"{BASE}/run_demo_rest/logs/stdout")).text
    assert (await client.get(f"{BASE}/run_demo_rest/cloud/cloud_3d_9.png")).status_code == 404


async def test_no_startup_migration(tmp_path):
    config = Settings(
        _env_file=None,
        database_path=tmp_path / "absent.db",
        workspace_root=tmp_path / "workspace",
        service_log_root=tmp_path / "logs",
    )
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        await RunRepository(config.database_path).initialize()
    app = create_app(config)
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        async with app.router.lifespan_context(app):
            pass
    assert not config.database_path.exists()


async def test_migrations_and_recovery(settings):
    repo = RunRepository(settings.database_path)
    await repo.initialize()
    row = await repo.submit("run_alpha", {"model_filename": "part.stp"})
    await repo.claim()

    restarted = RunRepository(settings.database_path)
    await restarted.initialize()
    await restarted.recover_running()
    assert (await restarted.get(row["run_id"]))["status"] == "unknown"
    assert await restarted.claim() is None

    assert "No new upgrade operations" in migrate(settings.database_path, "check").stdout
    migrate(settings.database_path, "downgrade", "base")
    migrate(settings.database_path, "upgrade", "head")
    await restarted.initialize()
    await repo.close()
    await restarted.close()


async def test_revision_mismatch_refused(settings):
    repo = RunRepository(settings.database_path)

    def change_revision():
        with repo.database.sessions.begin() as session:
            session.execute(text("UPDATE alembic_version SET version_num='old_revision'"))

    await asyncio.to_thread(change_revision)
    with pytest.raises(RuntimeError, match="版本"):
        await repo.initialize()
    await repo.close()


@needs_posix_worker
def test_single_worker_lock(settings):
    worker = load_worker()
    with worker.worker_lock(settings.database_path):
        with pytest.raises(RuntimeError, match="已有 Worker"):
            with worker.worker_lock(settings.database_path):
                pass
    with worker.worker_lock(settings.database_path):
        pass


@needs_posix_worker
async def test_standalone_worker_entrypoint(client, settings, model_file, tmp_path):
    worker = load_worker()
    run = await submit(client, model=model_file)
    env = os.environ.copy()
    for key in (
        "database_path",
        "workspace_root",
        "service_log_root",
        "test_sleep_seconds",
        "test_exit_code",
    ):
        env[f"CAE_{key.upper()}"] = str(getattr(settings, key))
    env.pop("CAE_GUIERUNNER_PATH", None)  # Blank/unset selects the bundled example script.
    with (tmp_path / "worker-console.log").open("wb") as output:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "worker.py",
            env=env,
            stdout=output,
            stderr=output,
        )
        try:
            for _ in range(200):
                state = (await client.get(run["status_url"])).json()["status"]
                if state == "succeeded":
                    break
                assert process.returncode is None
                await asyncio.sleep(0.05)
            else:
                pytest.fail("standalone worker did not finish the task")
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                pytest.fail("worker failed to stop on SIGTERM")
    assert process.returncode == 0
    assert list(settings.service_log_root.glob("worker-*.log"))
    with worker.worker_lock(settings.database_path):
        pass


@needs_posix_worker
@pytest.mark.skipif(
    os.environ.get("CAE_RUN_SLOW_TESTS") != "1", reason="opt-in real 60-second execution"
)
async def test_real_sixty_second_flow(client, settings, model_file):
    worker = load_worker()
    assert Settings(_env_file=None).test_sleep_seconds == 60
    run = await submit(client, model=model_file)
    started = time.monotonic()
    task = asyncio.create_task(
        worker.GuieWorker(settings.model_copy(update={"test_sleep_seconds": 60})).run_once()
    )
    await asyncio.sleep(0.5)
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get(run["status_url"])).json()["status"] == "running"
    await task
    assert time.monotonic() - started >= 60
    assert (await client.get(run["status_url"])).json()["status"] == "succeeded"
