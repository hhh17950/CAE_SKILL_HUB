import asyncio
import os
import sys
import time

import pytest
from sqlalchemy import text

from core.config import Settings
from main import create_app
from storage.repository import RunRepository
from tests.conftest import migrate
from worker import GuieWorker, worker_lock

BASE = "/api/v1/guie-runs"


async def submit(client, body):
    response = await client.post(BASE, json=body)
    assert response.status_code == 202, response.text
    run = response.json()
    assert response.headers["Location"] == run["status_url"]
    return run


async def test_success_env_outputs_and_ownership(client, settings, body):
    run = await submit(client, body)
    assert run["status"] == "queued"
    assert (await client.get(run["result_url"])).status_code == 409
    assert await GuieWorker(settings).run_once()
    result = (await client.get(run["status_url"])).json()
    assert result["status"] == "succeeded" and result["exit_code"] == 0
    cloud = (await client.get(run["result_url"])).json()["cloud_info"]
    assert cloud == {
        "test_only": True,
        "model_name": "part.stp",
        **{k: v for k, v in body.items() if k not in ("model_path", "project_dir")},
    }
    for kind, phrase in (
        ("stdout", "test completed"),
        ("stderr", "capture ready"),
        ("jusmar", "test completed"),
    ):
        log = await client.get(run["log_urls"][kind])
        assert log.status_code == 200 and phrase in log.text
    for url in [run["status_url"], run["result_url"], *run["log_urls"].values()]:
        assert (
            await client.get(url, headers={"Authorization": "Bearer beta-token"})
        ).status_code == 404
    assert list(settings.service_log_root.glob("api-*.log"))


async def test_each_submission_is_new_task(client, settings, body):
    first, second = await submit(client, body), await submit(client, body)
    assert first["run_id"] != second["run_id"]
    worker = GuieWorker(settings)
    assert await worker.run_once()
    assert await worker.run_once()
    assert not await worker.run_once()
    for run in (first, second):
        assert (settings.project_root / "existing-project").is_dir()
        assert not (settings.run_root / run["run_id"] / "project").exists()
        assert (settings.task_log_root / run["run_id"] / "stdout.log").is_file()


@pytest.mark.parametrize(
    "field,value",
    [
        ("density", 1.5),
        ("density", True),
        ("young_modulus", -1),
        ("poisson_ratio", 0.5),
        ("number_of_roots", 0),
        ("extra", "x"),
    ],
)
async def test_validation(client, body, field, value):
    response = await client.post(BASE, json={**body, field: value})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


async def test_auth_and_limits(client, body):
    assert (
        await client.post(BASE, json=body, headers={"Authorization": "Bearer invalid"})
    ).status_code == 401
    response = await client.post(BASE, content="x" * 70000)
    assert response.status_code == 413


async def test_path_and_symlink_escape(client, settings, body, tmp_path):
    outside = tmp_path / "outside.stp"
    outside.write_text("outside")
    link = settings.model_root / "escape.stp"
    link.symlink_to(outside)
    for path in (outside, link, "relative.stp", settings.model_root / "missing.stp"):
        assert (await client.post(BASE, json={**body, "model_path": str(path)})).status_code == 422


async def test_required_existing_project_dir(client, settings, body, tmp_path):
    assert (
        await client.post(BASE, json={k: v for k, v in body.items() if k != "project_dir"})
    ).status_code == 422
    outside = tmp_path / "other-project"
    outside.mkdir()
    for value in ("relative-project", str(outside), str(settings.project_root / "missing")):
        assert (await client.post(BASE, json={**body, "project_dir": value})).status_code == 422
    link = settings.project_root / "escape"
    link.symlink_to(outside, target_is_directory=True)
    assert (await client.post(BASE, json={**body, "project_dir": str(link)})).status_code == 422


async def test_supplied_output_paths_take_precedence(client, settings, body):
    output_dir = settings.output_root / "chosen"
    output_dir.mkdir()
    custom_log = output_dir / "solver.log"
    custom_json = output_dir / "result.json"
    run = await submit(
        client, {**body, "jusmar_log_path": str(custom_log), "cloud_info_path": str(custom_json)}
    )
    assert await GuieWorker(settings).run_once()
    assert custom_log.is_file() and custom_json.is_file()
    assert not (settings.task_log_root / run["run_id"] / "jusmar.log").exists()
    assert not (settings.run_root / run["run_id"] / "cloud_info.json").exists()
    assert "test completed" in (await client.get(run["log_urls"]["jusmar"])).text
    assert (await client.get(run["result_url"])).json()["cloud_info"]["number_of_roots"] == 10
    assert (
        await client.get(run["result_url"], headers={"Authorization": "Bearer beta-token"})
    ).status_code == 404


async def test_reject_unsafe_output_paths(client, settings, body, tmp_path):
    existing = settings.output_root / "existing.log"
    existing.write_text("keep", encoding="utf-8")
    cases = (
        {"jusmar_log_path": "relative.log"},
        {"jusmar_log_path": str(tmp_path / "outside.log")},
        {"jusmar_log_path": str(existing)},
        {"cloud_info_path": str(settings.output_root / "wrong.txt")},
        {"cloud_info_path": str(settings.output_root / "missing-parent" / "result.json")},
        {
            "jusmar_log_path": str(settings.output_root / "same.json"),
            "cloud_info_path": str(settings.output_root / "same.json"),
        },
    )
    for fields in cases:
        assert (await client.post(BASE, json={**body, **fields})).status_code == 422
    assert existing.read_text(encoding="utf-8") == "keep"


async def test_reject_reused_custom_output_path(client, settings, body):
    selected = str(settings.output_root / "reserved.json")
    await submit(client, {**body, "cloud_info_path": selected})
    response = await client.post(BASE, json={**body, "jusmar_log_path": selected})
    assert response.status_code == 422
    assert response.json()["code"] == "OUTPUT_PATH_IN_USE"
    assert not (settings.output_root / "reserved.json").exists()


async def test_failure_and_timeout(client, settings, body):
    for update, status, code in (
        ({"test_exit_code": 7}, "failed", 7),
        ({"test_sleep_seconds": 60, "run_timeout_seconds": 1}, "timed_out", -9),
    ):
        run = await submit(client, body)
        await GuieWorker(settings.model_copy(update=update)).run_once()
        result = (await client.get(run["status_url"])).json()
        assert result["status"] == status and result["exit_code"] == code
        assert (await client.get(run["result_url"])).status_code == 409


async def test_file_removed_after_enqueue(client, settings, body):
    run = await submit(client, body)
    (settings.model_root / "part.stp").unlink()
    await GuieWorker(settings).run_once()
    assert (await client.get(run["status_url"])).json()["status"] == "failed"


async def test_project_removed_after_enqueue(client, settings, body):
    run = await submit(client, body)
    (settings.project_root / "existing-project").rmdir()
    await GuieWorker(settings).run_once()
    assert (await client.get(run["status_url"])).json()["status"] == "failed"


async def test_cancel_marks_unknown(client, settings, body):
    run = await submit(client, body)
    task = asyncio.create_task(
        GuieWorker(settings.model_copy(update={"test_sleep_seconds": 60})).run_once()
    )
    log = settings.task_log_root / run["run_id"] / "stdout.log"
    for _ in range(100):
        if log.exists() and "started" in log.read_text():
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("script did not start")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await client.get(run["status_url"])).json()["status"] == "unknown"


async def test_result_missing_or_invalid(client, settings, body):
    run = await submit(client, body)
    await GuieWorker(settings).run_once()
    result = settings.run_root / run["run_id"] / "cloud_info.json"
    result.write_text("invalid json")
    assert (await client.get(run["result_url"])).status_code == 500
    result.unlink()
    assert (await client.get(run["result_url"])).status_code == 404


async def test_no_startup_migration(tmp_path):
    config = Settings(
        _env_file=None, database_path=tmp_path / "absent.db", service_log_root=tmp_path / "logs"
    )
    for component in (RunRepository(config.database_path), GuieWorker(config).store):
        with pytest.raises(RuntimeError, match="alembic upgrade head"):
            await component.initialize()
    app = create_app(config)
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        async with app.router.lifespan_context(app):
            pass
    assert not config.database_path.exists()


async def test_migrations_and_recovery(settings, body):
    repo = RunRepository(settings.database_path)
    await repo.initialize()
    row = await repo.submit("alpha", body)
    await repo.claim()
    restarted = RunRepository(settings.database_path)
    await restarted.initialize()
    await restarted.recover_running()
    assert (await restarted.get("alpha", row["run_id"]))["status"] == "unknown"
    assert await restarted.claim() is None
    assert "No new upgrade operations" in migrate(settings.database_path, "check").stdout
    migrate(settings.database_path, "downgrade", "base")
    migrate(settings.database_path, "upgrade", "head")
    await restarted.initialize()


async def test_revision_mismatch_refused(settings):
    repo = RunRepository(settings.database_path)

    def change_revision():
        with repo.database.sessions.begin() as session:
            session.execute(text("UPDATE alembic_version SET version_num='old_revision'"))

    await asyncio.to_thread(change_revision)
    with pytest.raises(RuntimeError, match="版本"):
        await repo.initialize()
    await repo.close()


def test_single_worker_lock(settings):
    with worker_lock(settings.database_path):
        with pytest.raises(RuntimeError, match="已有 Worker"):
            with worker_lock(settings.database_path):
                pass
    with worker_lock(settings.database_path):
        pass


async def test_standalone_worker_entrypoint(client, settings, body, tmp_path):
    run = await submit(client, body)
    env = os.environ.copy()
    for key in (
        "database_path",
        "run_root",
        "task_log_root",
        "service_log_root",
        "model_root",
        "project_root",
        "output_root",
        "test_sleep_seconds",
    ):
        env[f"CAE_{key.upper()}"] = str(getattr(settings, key))
    with (tmp_path / "worker-console.log").open("wb") as output:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "worker.py",
            env=env,
            stdout=output,
            stderr=output,
        )
        try:
            for _ in range(100):
                state = (await client.get(run["status_url"])).json()["status"]
                if state == "succeeded":
                    break
                assert process.returncode is None
                await asyncio.sleep(0.05)
            else:
                pytest.fail("standalone worker did not finish task")
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
    with worker_lock(settings.database_path):
        pass


@pytest.mark.skipif(
    os.environ.get("CAE_RUN_SLOW_TESTS") != "1", reason="opt-in real 60-second execution"
)
async def test_real_sixty_second_flow(client, settings, body):
    assert Settings(_env_file=None).test_sleep_seconds == 60
    run = await submit(client, body)
    started = time.monotonic()
    task = asyncio.create_task(
        GuieWorker(settings.model_copy(update={"test_sleep_seconds": 60})).run_once()
    )
    await asyncio.sleep(0.5)
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get(run["status_url"])).json()["status"] == "running"
    await task
    assert time.monotonic() - started >= 60
    assert (await client.get(run["status_url"])).json()["status"] == "succeeded"
