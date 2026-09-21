import asyncio
import threading
import time

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from storage.database import run_in_thread
from storage.repository import RunRepository


async def test_database_uses_delete_journal(settings, body):
    repo = RunRepository(settings.database_path)
    await repo.initialize()
    await repo.submit("alpha", body)

    def check_mode():
        with repo.database.sessions() as session:
            assert session.execute(text("PRAGMA journal_mode")).scalar_one() == "delete"
            assert session.execute(text("PRAGMA quick_check")).scalar_one() == "ok"

    await run_in_thread(check_mode)
    await repo.close()


async def test_concurrent_claim_is_atomic(settings, body):
    repo = RunRepository(settings.database_path)
    row = await repo.submit("alpha", body)
    results = await asyncio.gather(*(repo.claim() for _ in range(8)))
    claimed = [item for item in results if item is not None]
    assert claimed == [(row["run_id"], body)]
    await repo.close()


async def test_connections_stay_in_worker_thread(settings, body):
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
    await repo.submit("alpha", body)
    await repo.claim()
    await repo.close()
    assert len(threads) == 2 and all(thread != main_thread for thread in threads)


async def test_failed_transaction_rolls_back(settings, body):
    repo = RunRepository(settings.database_path)
    row = await repo.submit("alpha", body)
    await repo.claim()
    with pytest.raises(IntegrityError):
        await repo.finish(row["run_id"], "invalid_status", 0, None)
    assert (await repo.get("alpha", row["run_id"]))["status"] == "running"
    await repo.finish(row["run_id"], "succeeded", 0, None)
    assert (await repo.get("alpha", row["run_id"]))["status"] == "succeeded"
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
