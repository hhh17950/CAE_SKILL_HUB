"""Durable single-host job repository. Each transaction owns its connection.

All document/status writes go through _save; the indexed status and public view
are updated in the same transaction. GUI side effects are outside this database.
"""

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
import anyio

from app.errors import DomainError, not_found
from app.schemas.analysis import AssetView, RunResult, RunView, StepView
from app.schemas.common import ExecutionError
from app.storage.memory import new_id, now


@dataclass(frozen=True)
class AssetRecord:
    owner: str
    path: Path
    view: AssetView


async def one(db, sql: str, args=()):
    async with db.execute(sql, args) as cursor:
        return await cursor.fetchone()


class JobRepository:
    def __init__(self, path: Path, max_records: int = 10000):
        self.path = path.resolve()
        self.max_records = max_records

    async def initialize(self):
        await anyio.to_thread.run_sync(lambda: self.path.parent.mkdir(parents=True, exist_ok=True))
        async with aiosqlite.connect(self.path, timeout=30) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            row = await one(db, "PRAGMA user_version")
            if row[0] not in (0, 1):
                raise RuntimeError("不支持此数据库版本，禁止自动降级")
            if row[0] == 0:
                migration = Path(__file__).resolve().parents[2] / "migrations/001_jobs.sql"
                sql = await anyio.Path(migration).read_text()
                await db.executescript(sql)

    @asynccontextmanager
    async def connection(self, *, write=False):
        async with aiosqlite.connect(self.path, timeout=30, isolation_level=None) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            if write:
                await db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                if write:
                    await db.commit()
            except BaseException:
                if write:
                    await db.rollback()
                raise

    async def _capacity(self, db):
        row = await one(
            db, "SELECT (SELECT COUNT(*) FROM assets) + (SELECT COUNT(*) FROM analysis_runs)"
        )
        if row[0] >= self.max_records:
            raise DomainError("CAPACITY_EXCEEDED", "持久化记录容量已达到配置上限", 429)

    async def add_asset(self, record: AssetRecord):
        async with self.connection(write=True) as db:
            await self._capacity(db)
            await db.execute(
                "INSERT INTO assets VALUES (?, ?, ?, ?)",
                (
                    record.view.asset_id,
                    record.owner,
                    str(record.path),
                    record.view.model_dump_json(),
                ),
            )

    async def asset(self, owner: str, asset_id: str) -> AssetRecord:
        async with self.connection() as db:
            row = await one(
                db, "SELECT * FROM assets WHERE asset_id=? AND owner=?", (asset_id, owner)
            )
        if row is None:
            raise not_found("几何资产")
        return AssetRecord(owner, Path(row["path"]), AssetView.model_validate_json(row["document"]))

    async def run(self, owner: str, run_id: str) -> RunView:
        async with self.connection() as db:
            row = await one(
                db, "SELECT document FROM analysis_runs WHERE run_id=? AND owner=?", (run_id, owner)
            )
        if row is None:
            raise not_found("分析任务")
        return RunView.model_validate_json(row[0])

    async def replay(self, owner: str, key: str, request_hash: str) -> RunView | None:
        async with self.connection() as db:
            return await self._replay(db, owner, key, request_hash)

    async def _replay(self, db, owner, key, request_hash):
        row = await one(
            db,
            "SELECT request_hash, document FROM analysis_runs WHERE owner=? AND idempotency_key=?",
            (owner, key),
        )
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键不能提交不同参数", 409)
        return RunView.model_validate_json(row["document"])

    async def submit(self, owner, key, request_hash, raw_request: str, view: RunView) -> RunView:
        async with self.connection(write=True) as db:
            existing = await self._replay(db, owner, key, request_hash)
            if existing:
                return existing
            await self._capacity(db)
            await db.execute(
                "INSERT INTO analysis_runs(run_id,owner,idempotency_key,request_hash,raw_request,status,created_at,document) VALUES (?,?,?,?,?,?,?,?)",
                (
                    view.run_id,
                    owner,
                    key,
                    request_hash,
                    raw_request,
                    view.status,
                    view.created_at.isoformat(),
                    view.model_dump_json(),
                ),
            )
        return view

    async def _save(self, db, view: RunView, lease_until: float | None = None):
        view.updated_at = now()
        await db.execute(
            "UPDATE analysis_runs SET status=?, attempt_id=?, lease_until=?, document=? WHERE run_id=?",
            (view.status, view.attempt_id, lease_until, view.model_dump_json(), view.run_id),
        )

    async def _expire(self, db):
        async with db.execute(
            "SELECT document FROM analysis_runs WHERE status IN ('running','cancelling') AND lease_until < ?",
            (time.time(),),
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            view = RunView.model_validate_json(row[0])
            view.status = "unknown"
            view.error = ExecutionError(
                code="WORKER_LEASE_EXPIRED", detail="执行租约过期，底层是否停止待核对；不会自动重放"
            )
            for step in view.steps:
                if step.status == "running":
                    step.status = "unknown"
            await self._save(db, view)

    async def claim(self, worker_id: str, lease_seconds: float) -> tuple[str, RunView] | None:
        async with self.connection(write=True) as db:
            await self._expire(db)
            # One execution slot per database. Unknown work keeps that slot quarantined.
            active = await one(
                db,
                "SELECT 1 FROM analysis_runs WHERE status IN ('running','cancelling','unknown') LIMIT 1",
            )
            if active:
                return None
            row = await one(
                db,
                "SELECT owner, document FROM analysis_runs WHERE status='queued' ORDER BY created_at,run_id LIMIT 1",
            )
            if row is None:
                return None
            view = RunView.model_validate_json(row["document"])
            view.status = "running"
            view.attempt_id = new_id("attempt")
            view.worker_id = worker_id
            await self._save(db, view, time.time() + lease_seconds)
            return row["owner"], view

    async def heartbeat(
        self, run_id, attempt_id, lease_seconds, steps: list[StepView] | None = None
    ) -> RunView | None:
        async with self.connection(write=True) as db:
            row = await one(db, "SELECT * FROM analysis_runs WHERE run_id=?", (run_id,))
            if (
                not row
                or row["attempt_id"] != attempt_id
                or row["status"] not in ("running", "cancelling")
                or row["lease_until"] < time.time()
            ):
                return None
            view = RunView.model_validate_json(row["document"])
            if steps is not None:
                view.steps = steps
                view.current_step = next(
                    (s.name for s in steps if s.status == "running"), view.current_step
                )
            await self._save(db, view, time.time() + lease_seconds)
            return view

    async def finish(
        self,
        run_id,
        attempt_id,
        status,
        *,
        steps=None,
        result: RunResult | None = None,
        error: ExecutionError | None = None,
        runtime_versions=None,
    ) -> bool:
        async with self.connection(write=True) as db:
            row = await one(db, "SELECT * FROM analysis_runs WHERE run_id=?", (run_id,))
            if (
                not row
                or row["attempt_id"] != attempt_id
                or row["status"] not in ("running", "cancelling")
                or row["lease_until"] < time.time()
            ):
                return False
            view = RunView.model_validate_json(row["document"])
            view.status, view.result, view.error = status, result, error
            if steps is not None:
                view.steps = steps
            if runtime_versions:
                view.runtime_versions = runtime_versions
            for step in view.steps:
                if step.status == "running":
                    step.status = (
                        "cancelled"
                        if status == "cancelled"
                        else "unknown"
                        if status == "unknown"
                        else "failed"
                    )
            failed_step = next((step.name for step in view.steps if step.status == "failed"), None)
            if failed_step:
                view.current_step = failed_step
            if status == "succeeded":
                view.current_step = None
            await self._save(db, view)
            return True

    async def cancel(self, owner, run_id) -> RunView:
        async with self.connection(write=True) as db:
            row = await one(
                db, "SELECT * FROM analysis_runs WHERE owner=? AND run_id=?", (owner, run_id)
            )
            if row is None:
                raise not_found("分析任务")
            view = RunView.model_validate_json(row["document"])
            if view.status == "unknown":
                raise DomainError("EXECUTION_UNCONFIRMED", "底层执行状态待核对，不能确认取消", 409)
            if view.status in ("queued", "running", "cancelling"):
                view.cancel_requested = True
                view.status = "cancelled" if view.status == "queued" else "cancelling"
                await self._save(db, view, row["lease_until"])
            return view

    async def resolve_unknown(self, run_id: str) -> RunView:
        """Operator-only transition after independently verifying execution has stopped."""
        async with self.connection(write=True) as db:
            await self._expire(db)
            row = await one(db, "SELECT document FROM analysis_runs WHERE run_id=?", (run_id,))
            if row is None:
                raise not_found("分析任务")
            view = RunView.model_validate_json(row[0])
            if view.status != "unknown":
                raise DomainError("INVALID_STATE", "仅 unknown 任务允许人工解除隔离", 409)
            view.status = "failed"
            view.error = ExecutionError(
                code="OPERATOR_CONFIRMED_STOP",
                detail="运维人员已确认旧执行停止；本次任务不自动重试",
            )
            await self._save(db, view)
            return view
