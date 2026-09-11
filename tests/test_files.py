from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app.main import create_app
from tests.helpers import key
from tests.legacy_client import wait_for


async def test_invalid_upload_and_oversized_file(client, app):
    for filename in ["a.exe", "../x.stp", "path\\x.stp"]:
        response = await client.post(
            "/api/v1/files", files={"file": (filename, b"mock")}, headers=key()
        )
        assert response.status_code == 422, response.text
    app.state.settings.max_file_bytes = 10
    response = await client.post(
        "/api/v1/files", files={"file": ("a.stp", b"x" * 11)}, headers=key()
    )
    assert response.status_code == 413


async def test_chunked_body_is_bounded(client, settings):
    async def chunks():
        for _ in range((settings.max_file_bytes + 65536) // 65536 + 2):
            yield b"x" * 65536

    response = await client.post(
        "/api/v1/geometry-imports",
        content=chunks(),
        headers={"Content-Type": "application/json", **key()},
    )
    assert response.status_code == 413, response.text


async def test_server_paths_disabled_by_default(client):
    response = await client.post(
        "/api/v1/geometry-imports",
        headers=key(),
        json={"source": {"type": "server_path", "path": "/tmp/test.stp"}},
    )
    assert response.status_code == 422


async def test_server_path_escape_and_staged_file(settings, tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    sample = root / "test.stp"
    sample.write_bytes(b"mock")
    outside = tmp_path / "outside.stp"
    outside.write_bytes(b"other")
    (root / "link.stp").symlink_to(outside)
    config = settings.model_copy(update={"allow_server_paths": True, "server_path_root": root})
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer alpha-token"},
        ) as client:
            for path in [outside, root / "link.stp", root / "../outside.stp"]:
                response = await client.post(
                    "/api/v1/geometry-imports",
                    headers=key(),
                    json={"source": {"type": "server_path", "path": str(path)}},
                )
                assert response.status_code == 403
            response = await client.post(
                "/api/v1/geometry-imports",
                headers=key(),
                json={"source": {"type": "server_path", "path": str(sample)}},
            )
            result = await wait_for(client, response.headers["location"])
            assert result["result"]["file_name"] == "test.stp"
            files = list(app.state.container.shared.store.files.values())
            assert files[0].path != Path(sample)
            assert files[0].path.read_bytes() == b"mock"
