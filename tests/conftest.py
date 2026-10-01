"""Shared fixtures: a migrated temporary database, an upload and an ASGI client.

The service has no authentication layer and no caller-supplied paths: every test drives the
real multipart submission and the real task directory created by ``services/paths.py``.
"""

import os
import subprocess
import sys
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from core.config import Settings
from main import create_app

SUBMIT_URL = "/api/v1/guie-runs/modal"
GEOMETRY = b"test geometry\x00\xff\n"
DEFAULT_PARAMETERS = {
    "young_modulus": "2.0e11",
    "poisson_ratio": "0.3",
    "density": "7850",
    "number_of_roots": "10",
}


def migrate(database, *args):
    """Run the Alembic CLI, exactly as deployment does; the app itself never migrates."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "CAE_DATABASE_PATH": str(database)},
        capture_output=True,
        text=True,
        check=True,
    )


async def submit_modal(client, model=("part.stp", GEOMETRY, "application/octet-stream"), **fields):
    """POST one multipart submission; ``fields`` override the default physical parameters."""
    data = {**DEFAULT_PARAMETERS, **fields}
    files = {"model_file": model} if model is not None else None
    return await client.post(SUBMIT_URL, data=data, files=files)


@pytest.fixture
def settings(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = Settings(
        _env_file=None,
        database_path=tmp_path / "guie.db",
        workspace_root=workspace,
        service_log_root=tmp_path / "service-logs",
        test_sleep_seconds=0.01,
    )
    migrate(config.database_path, "upgrade", "head")
    return config


@pytest.fixture
def model_file():
    """Uploaded geometry: these bytes must reach the task directory unchanged."""
    return ("part.stp", GEOMETRY, "application/octet-stream")


@asynccontextmanager
async def running_app(settings):
    """Run the real lifespan (store version check + MCP task) around an ASGI client."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


@pytest_asyncio.fixture
async def client(settings):
    """The default application client, bound to the migrated temporary database."""
    async with running_app(settings) as client:
        yield client
