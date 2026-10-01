"""End-to-end runs: multipart submission -> real Worker -> results, logs and the CLI.

``worker.py`` imports ``fcntl`` and ``services/executor.py`` calls ``os.killpg``, so every case
that actually executes a script is POSIX-only and skipped on Windows. Cases that only exercise
the API, the repository or the Alembic CLI still run everywhere.
"""

import asyncio
import os
import sys
import time

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
