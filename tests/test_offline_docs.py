import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from main import create_app


def test_vendored_asset_checksums():
    root = Path(__file__).resolve().parents[1] / "static" / "docs"
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["version"] == "5.33.0"
    for name, expected in manifest["files_sha256"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected


class AssetLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and "src" in attributes:
            self.links.append(attributes["src"])
        if tag == "link" and "href" in attributes:
            self.links.append(attributes["href"])


async def test_swagger_assets_are_local(client):
    response = await client.get("/docs")
    assert response.status_code == 200
    parser = AssetLinks()
    parser.feed(response.text)
    assert len(parser.links) == 3
    for link in parser.links:
        assert link.startswith("/static/docs/") and "://" not in link
        asset = await client.get(link)
        assert asset.status_code == 200 and len(asset.content) > 100
    assert '"validatorUrl": null' in response.text
    assert "url: '/openapi.json'" in response.text
    assert (await client.get("/openapi.json")).status_code == 200
    assert (await client.get("/redoc")).status_code == 404
    assert (await client.get("/static/logs/secret.log")).status_code == 404
    assert (await client.get("/static/docs/%2e%2e/logs/secret.log")).status_code == 404


async def test_swagger_reverse_proxy_prefix(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app, root_path="/cae"), base_url="http://test"
        ) as client:
            response = await client.get("/cae/docs")
            assert response.status_code == 200
            parser = AssetLinks()
            parser.feed(response.text)
            for link in parser.links:
                assert link.startswith("/cae/static/docs/")
                assert (await client.get(link)).status_code == 200
            assert "url: '/cae/openapi.json'" in response.text
            schema = (await client.get("/cae/openapi.json")).json()
            assert {"url": "/cae"} in schema["servers"]
