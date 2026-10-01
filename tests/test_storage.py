"""Repository guarantees: SQLite configuration, atomic claim, thread isolation.

These tests touch ``storage/`` only; they never import ``worker.py`` (POSIX-only) or the API.
The repository has no owner dimension: the public route always stores ``owner="public"`` and
every read is keyed by ``run_id`` alone.
"""

import asyncio
import threading
import time

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from storage.database import run_in_thread
from storage.repository import RunRepository

RUN_ID = "run_alpha"
PARAMETERS = {
    "young_modulus": 2.0e11,
    "poisson_ratio": 0.3,
    "density": 7850,
    "number_of_roots": 10,
    "model_filename": "part.stp",
}


async def test_database_uses_delete_journal(settings):
    repo = RunRepository(settings.database_path)
    await repo.initialize()
    await repo.submit(RUN_ID, PARAMETERS)

    def check_mode():
        with repo.database.sessions() as session:
            # Navicat must be able to read the file, so the service never switches to WAL.
            assert session.execute(text("PRAGMA journal_mode")).scalar_one() == "delete"
            assert session.execute(text("PRAGMA quick_check")).scalar_one() == "ok"

    await run_in_thread(check_mode)
    await repo.close()


async def test_concurrent_claim_is_atomic(settings):
    repo = RunRepository(settings.database_path)
    row = await repo.submit(RUN_ID, PARAMETERS)
    results = await asyncio.gather(*(repo.claim() for _ in range(8)))
    claimed = [item for item in results if item is not None]
    assert claimed == [(row["run_id"], PARAMETERS)]
    await repo.close()


async def test_connections_stay_in_worker_thread(settings):
    repo = RunRepository(settings.database_path)
    main_thread = threading.get_ident()
    threads = []

    def connected(connection, record):
        record.info["thread"] = threading.get_ident()
        threads.append(record.info["thread"])

    def closed(connection, record):
        assert record.info["thread"] == threading.get_ident()

    event.listen(repo.database.engine, "connect", connected)
    event.listen(repo.database.engine, "close", closed)
    await repo.submit(RUN_ID, PARAMETERS)
    await repo.claim()
    await repo.close()
    # NullPool: one connection per operation, both created and returned off the event loop.
    assert len(threads) == 2 and all(thread != main_thread for thread in threads)


async def test_failed_transaction_rolls_back(settings):
    repo = RunRepository(settings.database_path)
    row = await repo.submit(RUN_ID, PARAMETERS)
    await repo.claim()
    # The status CHECK constraint rejects the value, and the failed UPDATE must leave
    # the row exactly as it was.
    with pytest.raises(IntegrityError):
        await repo.finish(row["run_id"], "invalid_status", 0, None)
    assert (await repo.get(row["run_id"]))["status"] == "running"
    await repo.finish(row["run_id"], "succeeded", 0, None)
    assert (await repo.get(row["run_id"]))["status"] == "succeeded"
    await repo.close()


async def test_finish_only_touches_a_running_row(settings):
    repo = RunRepository(settings.database_path)
    row = await repo.submit(RUN_ID, PARAMETERS)
    # A queued row must not be finalised: only the worker that claimed it may finish it.
    await repo.finish(row["run_id"], "succeeded", 0, None)
    assert (await repo.get(row["run_id"]))["status"] == "queued"
    await repo.claim()
    await repo.finish(row["run_id"], "failed", 3, "脚本退出码非 0")
    stored = await repo.get(row["run_id"])
    assert stored["status"] == "failed" and stored["exit_code"] == 3
    await repo.close()


async def test_recover_running_marks_unknown(settings):
    repo = RunRepository(settings.database_path)
    row = await repo.submit(RUN_ID, PARAMETERS)
    await repo.claim()
    await repo.recover_running()
    stored = await repo.get(row["run_id"])
    assert stored["status"] == "unknown" and stored["error"]
    assert await repo.claim() is None
    await repo.close()


async def test_thread_operation_does_not_block_loop():
    entered = threading.Event()
    finished = threading.Event()

    def operation():
        entered.set()
        time.sleep(0.2)
        finished.set()

    task = asyncio.create_task(run_in_thread(operation))
    while not entered.is_set():
        await asyncio.sleep(0.001)
    assert not finished.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()  # Cancellation must not leave a transaction running.
