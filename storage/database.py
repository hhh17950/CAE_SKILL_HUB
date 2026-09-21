"""SQLAlchemy owns all runtime connections and sessions.

Each repository operation creates/closes its session in one worker thread.
NullPool keeps this small SQLite service free of long-lived idle connections.
"""

import asyncio
from pathlib import Path

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


async def run_in_thread(operation, *args):
    """Wait for an in-flight transaction even when its caller is cancelled.

    Cancelling to_thread cannot stop the thread. Waiting prevents releasing the
    Worker lock/disposal while a claim or commit is still completing.
    """
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


class Database:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.engine = create_engine(
            URL.create("sqlite+pysqlite", database=str(self.path)),
            connect_args={"timeout": 30, "check_same_thread": True},
            poolclass=NullPool,
        )
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def close(self):
        self.engine.dispose()
