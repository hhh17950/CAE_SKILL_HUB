"""MCP mount contract: the streamable HTTP endpoint must be routable and usable.

Every regression here was observed in a real deployment or reproduction:

* ``mcp`` was missing from requirements.txt, so the import failed and /mcp silently
  disappeared (the ImportError was swallowed while the REST API kept serving).
* mounting the MCP app under its own internal path exposed /mcp/mcp;
* ``app.mount()`` never runs a sub-application lifespan, so the streamable HTTP session
  manager answered every request with 500 until the host lifespan started it;
* the SDK rejects unknown Host headers (421), which used to block the LAN address the service
  is actually reached at; Host validation is now disabled outright (see the test below);
* the mcp 2.x line removed ``mcp.server.fastmcp``, so a 1.x-style import left the endpoint
  unmounted; the last test drives the endpoint with the real client from the pinned SDK.
"""

import importlib.metadata
import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from main import MCP_PATH, create_app

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None, reason="mcp SDK unavailable"
)


def mcp_major_version() -> int:
    """0 when the SDK is missing; the client API used below only exists in the 2.x line."""
    try:
        return int(importlib.metadata.version("mcp").split(".")[0])
    except importlib.metadata.PackageNotFoundError:
        return 0


HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0.1"},
    },
}


@asynccontextmanager
async def running_app(settings, base_url="http://127.0.0.1"):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=base_url) as client:
            yield client


def rpc_body(response):
    """Read the JSON-RPC payload of a streamable HTTP reply (SSE or plain JSON)."""
    for line in response.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    return json.loads(response.text)


async def open_session(client):
    handshake = await client.post(MCP_PATH, json=INITIALIZE, headers=HEADERS)
    assert handshake.status_code == 200, handshake.text
    session_id = handshake.headers.get("mcp-session-id")
    assert session_id
    initialized = await client.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**HEADERS, "mcp-session-id": session_id},
    )
    assert initialized.status_code in (200, 202)
    return session_id


async def test_initialize_answers_on_the_documented_path(settings):
    async with running_app(settings) as client:
        response = await client.post(MCP_PATH, json=INITIALIZE, headers=HEADERS)
        assert response.status_code == 200, response.text
        result = rpc_body(response)["result"]
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"]


async def test_session_manager_runs_and_tools_are_listed(settings):
    async with running_app(settings) as client:
        session_id = await open_session(client)
        listed = await client.post(
            MCP_PATH,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={**HEADERS, "mcp-session-id": session_id},
        )
        assert listed.status_code == 200, listed.text
        tools = {tool["name"] for tool in rpc_body(listed)["result"]["tools"]}
        assert tools == {"submit_modal_run", "get_run_status", "get_run_result", "get_run_log"}


async def test_two_apps_in_one_process_both_serve_mcp(settings):
    async with running_app(settings) as first:
        assert (await first.post(MCP_PATH, json=INITIALIZE, headers=HEADERS)).status_code == 200
    async with running_app(settings) as second:
        assert (await second.post(MCP_PATH, json=INITIALIZE, headers=HEADERS)).status_code == 200


async def test_mcp_path_is_not_doubled(settings):
    async with running_app(settings) as client:
        doubled = await client.post(f"{MCP_PATH}{MCP_PATH}", json=INITIALIZE, headers=HEADERS)
        assert doubled.status_code == 404


async def test_api_routes_keep_serving_next_to_the_mcp_mount(settings):
    async with running_app(settings) as client:
        assert (await client.get("/healthz")).status_code == 200
        assert (await client.get("/api/v1/guie-runs/run_missing")).status_code == 404


async def test_any_host_header_reaches_the_endpoint(settings):
    """Host validation is deliberately off: the SDK would answer 421 for every address except
    localhost and CAE_PUBLIC_BASE_URL, which breaks intranet callers that use a different
    address (IP instead of domain, another port, a reverse proxy) and never acted as access
    control anyway. Deployment-layer controls are what protect this endpoint.
    """
    for base_url in (
        "http://127.0.0.1",
        "http://192.168.16.128:8000",
        "http://cae.example:9000",
        "http://attacker.example",
    ):
        async with running_app(settings, base_url=base_url) as client:
            response = await client.post(MCP_PATH, json=INITIALIZE, headers=HEADERS)
            assert response.status_code == 200, f"{base_url}: {response.status_code}"


async def test_disabled_flag_mounts_nothing(settings):
    config = settings.model_copy(update={"mcp_enabled": False})
    async with running_app(config) as client:
        assert (await client.post(MCP_PATH, json=INITIALIZE, headers=HEADERS)).status_code == 404
        assert (await client.get("/healthz")).status_code == 200


async def test_get_run_result_hands_the_agent_fetchable_image_urls(settings, monkeypatch):
    """The agent must receive a URL per cloud image, and that URL must really serve the PNG.

    The finished run is fabricated (see ``services/scripts/simulate_cloud_run.py``) because this
    check has to pass on hosts where no CAE flow exists yet. The MCP tools build their own
    ``Settings()`` from the process environment - the same root ``.env`` the API and the Worker are
    started with - so this test points that environment at its temporary files.
    """
    from services.scripts.simulate_cloud_run import simulate

    run_id = "run_demo_cloud_test"
    base_url = "http://127.0.0.1"
    for name, value in (
        ("CAE_DATABASE_PATH", settings.database_path),
        ("CAE_WORKSPACE_ROOT", settings.workspace_root),
        ("CAE_SERVICE_LOG_ROOT", settings.service_log_root),
        ("CAE_PUBLIC_BASE_URL", base_url),
    ):
        monkeypatch.setenv(name, str(value))

    config = settings.model_copy(update={"public_base_url": base_url})
    await simulate(config, run_id, modes=2)

    async with running_app(config) as client:
        session_id = await open_session(client)
        called = await client.post(
            MCP_PATH,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_run_result", "arguments": {"run_id": run_id}},
            },
            headers={**HEADERS, "mcp-session-id": session_id},
        )
        assert called.status_code == 200, called.text
        result = rpc_body(called)["result"]
        assert result.get("isError") is not True
        text = next(item["text"] for item in result["content"] if item["type"] == "text")
        cloud_info = json.loads(text)["cloud_info"]
        assert sorted(cloud_info) == ["1", "2"]

        for entry in cloud_info.values():
            name = Path(entry["cloud_file_name"]).name
            url = f"{base_url}/api/v1/guie-runs/{run_id}/cloud/{name}"
            assert entry["image_url"] == url
            image = await client.get(url)
            assert image.status_code == 200, image.text
            assert image.headers["content-type"] == "image/png"
            assert image.content.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.skipif(mcp_major_version() < 2, reason="needs the mcp 2.x client API")
async def test_official_client_can_complete_the_handshake(settings):
    """The SDK the agent platform actually uses must be able to drive /mcp end to end."""
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        # httpx2 is what mcp 2.x uses for HTTP; ASGITransport keeps the test socket-free.
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as http_client:
            async with streamable_http_client(
                f"http://127.0.0.1{MCP_PATH}", http_client=http_client
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    handshake = await session.initialize()
                    assert handshake.server_info.name
                    listed = await session.list_tools()
                    assert {tool.name for tool in listed.tools} == {
                        "submit_modal_run",
                        "get_run_status",
                        "get_run_result",
                        "get_run_log",
                    }
