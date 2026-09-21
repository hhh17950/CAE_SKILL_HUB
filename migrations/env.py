"""Alembic CLI environment based on alembic init's standard scaffold.

Migrations are manual synchronous commands; runtime also uses SQLAlchemy + sqlite3.
"""

from alembic import context
from sqlalchemy import URL, create_engine, pool

from core.config import Settings
from storage.models import metadata

config = context.config
target_metadata = metadata
database = Settings().database_path.resolve()
url = URL.create("sqlite", database=str(database))


def run_migrations_offline():
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True, render_as_batch=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    database.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            # Traditional rollback journal for compatibility with local DB tools.
            # Change mode only in an explicit CLI operation, never on API startup.
            mode = connection.exec_driver_sql("PRAGMA journal_mode=DELETE").scalar_one()
            if mode.lower() != "delete":
                raise RuntimeError("无法切换 SQLite 日志模式，请先关闭其他数据库连接")
            connection.commit()
            context.configure(
                connection=connection, target_metadata=target_metadata, render_as_batch=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
