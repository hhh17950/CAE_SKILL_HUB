import os
import subprocess
import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from core.config import Settings
from main import create_app


def migrate(database, *args):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "CAE_DATABASE_PATH": str(database)},
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def settings(tmp_path):
    root = tmp_path / "models"
    root.mkdir()
    (root / "part.stp").write_text("test geometry", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "existing-project").mkdir()
    config = Settings(
        _env_file=None,
        database_path=tmp_path / "guie.db",
        run_root=tmp_path / "runs",
        task_log_root=tmp_path / "task-logs",
        service_log_root=tmp_path / "service-logs",
        model_root=root,
        project_root=workspace,
        output_root=workspace,
        test_sleep_seconds=0.01,
        api_tokens={"alpha-token": "alpha", "beta-token": "beta"},
    )
    migrate(config.database_path, "upgrade", "head")
    return config


@pytest.fixture
def body(settings):
    return {
        "model_path": str(settings.model_root / "part.stp"),
        "project_dir": str(settings.project_root / "existing-project"),
        "young_modulus": 200000000000.0,
        "poisson_ratio": 0.3,
        "density": 7850,
        "number_of_roots": 10,
    }


@pytest_asyncio.fixture
async def client(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer alpha-token"},
        ) as client:
            yield client
