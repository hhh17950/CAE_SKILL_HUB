"""SQLAlchemy metadata is the source for Alembic autogeneration."""

from sqlalchemy import CheckConstraint, Column, Index, Integer, MetaData, Table, Text

metadata = MetaData()

guie_runs = Table(
    "guie_runs",
    metadata,
    Column("run_id", Text, primary_key=True, nullable=False),
    Column("owner", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("request_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("started_at", Text),
    Column("finished_at", Text),
    Column("exit_code", Integer),
    Column("error", Text),
    CheckConstraint(
        "status IN ('queued', 'running', 'succeeded', 'failed', 'timed_out', 'unknown')",
        name="guie_runs_status_check",
    ),
)

Index("guie_runs_queue", guie_runs.c.status, guie_runs.c.created_at)
