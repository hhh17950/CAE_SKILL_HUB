"""SQLAlchemy persistence, with complete transactions offloaded to worker threads.

Public methods are async for API/Worker callers. No Session, Connection or live
Result leaves the synchronous helper that created it. Schema changes are CLI-only.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import OperationalError

from storage.database import Database, run_in_thread
from storage.models import guie_runs


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunRepository:
    def __init__(self, path: Path):
        self.database = Database(path)
        self.path = self.database.path

    async def initialize(self):
        await run_in_thread(self._initialize)

    def _initialize(self):
        instruction = "请先执行 alembic upgrade head"
        if not self.path.is_file():
            raise RuntimeError(f"数据库不存在，{instruction}")
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        head = ScriptDirectory.from_config(config).get_current_head()
        with self.database.sessions() as session:
            try:
                versions = list(
                    session.execute(text("SELECT version_num FROM alembic_version")).scalars()
                )
            except OperationalError as exc:
                raise RuntimeError(
                    f"数据库无法读取迁移版本，请检查文件权限和迁移状态；{instruction}"
                ) from exc
            if versions != [head]:
                raise RuntimeError(f"数据库版本 {versions} 与代码版本 {head} 不一致，{instruction}")

    async def close(self):
        await run_in_thread(self.database.close)

    async def submit(self, owner: str, parameters: dict) -> dict:
        return await run_in_thread(self._submit, owner, parameters)

    def _submit(self, owner: str, parameters: dict) -> dict:
        run_id = f"run_{uuid4().hex}"
        with self.database.sessions.begin() as session:
            session.execute(
                insert(guie_runs).values(
                    run_id=run_id,
                    owner=owner,
                    status="queued",
                    request_json=json.dumps(parameters),
                    created_at=utc_now(),
                )
            )
            return dict(
                session.execute(select(guie_runs).where(guie_runs.c.run_id == run_id))
                .mappings()
                .one()
            )

    async def get(self, owner: str, run_id: str) -> dict | None:
        return await run_in_thread(self._get, owner, run_id)

    def _get(self, owner: str, run_id: str) -> dict | None:
        with self.database.sessions() as session:
            row = (
                session.execute(
                    select(guie_runs).where(
                        guie_runs.c.run_id == run_id,
                        guie_runs.c.owner == owner,
                    )
                )
                .mappings()
                .one_or_none()
            )
            return dict(row) if row is not None else None

    async def claim(self) -> tuple[str, dict] | None:
        return await run_in_thread(self._claim)

    def _claim(self) -> tuple[str, dict] | None:
        with self.database.sessions.begin() as session:
            # Acquire the SQLite write reservation before selecting the next task.
            # Keep this transaction short; never hold it while running the script.
            session.execute(text("BEGIN IMMEDIATE"))
            row = (
                session.execute(
                    select(guie_runs.c.run_id, guie_runs.c.request_json)
                    .where(guie_runs.c.status == "queued")
                    .order_by(guie_runs.c.created_at, guie_runs.c.run_id)
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            parameters = json.loads(row["request_json"])
            session.execute(
                update(guie_runs)
                .where(guie_runs.c.run_id == row["run_id"])
                .values(status="running", started_at=utc_now())
            )
            return row["run_id"], parameters

    async def finish(self, run_id: str, status: str, exit_code: int | None, error: str | None):
        await run_in_thread(self._finish, run_id, status, exit_code, error)

    def _finish(self, run_id: str, status: str, exit_code: int | None, error: str | None):
        with self.database.sessions.begin() as session:
            session.execute(
                update(guie_runs)
                .where(
                    guie_runs.c.run_id == run_id,
                    guie_runs.c.status == "running",
                )
                .values(status=status, finished_at=utc_now(), exit_code=exit_code, error=error)
            )

    async def recover_running(self):
        await run_in_thread(self._recover_running)

    def _recover_running(self):
        with self.database.sessions.begin() as session:
            session.execute(
                update(guie_runs)
                .where(guie_runs.c.status == "running")
                .values(
                    status="unknown",
                    finished_at=utc_now(),
                    error="Worker 重启，脚本执行结果待人工核对",
                )
            )
