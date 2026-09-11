import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        upload_root=tmp_path / "uploads",
        database_path=tmp_path / "cae.db",
        asset_root=tmp_path / "assets",
        job_root=tmp_path / "jobs",
        enable_legacy_api=True,
        api_tokens={"alpha-token": "alpha", "beta-token": "beta"},
        mock_delay_seconds=0.001,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest_asyncio.fixture
async def client(app):
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer alpha-token"},
        ) as client:
            yield client
