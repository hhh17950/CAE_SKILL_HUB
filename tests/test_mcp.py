"""MCP mount contract: the streamable HTTP endpoint must be routable and usable.

Every regression here was observed in a real deployment or reproduction:

* ``mcp`` was missing from requirements.txt, so the import failed and /mcp silently
  disappeared (the ImportError was swallowed while the REST API kept serving).
* mounting the FastMCP app under its own internal path exposed /mcp/mcp;
* ``app.mount()`` never runs a sub-application lifespan, so the streamable HTTP session
  manager answered every request with 500 until the host lifespan started it;
* the SDK rejects unknown Host headers (421), which blocks the LAN address the service
  is actually reached at.
"""

import importlib.util
import json
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from main import MCP_PATH, create_app

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None, reason="mcp SDK unavailable"
)

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


async def test_public_base_url_host_is_allowed(settings):
    config = settings.model_copy(update={"public_base_url": "http://cae.example:9000"})
    async with running_app(config, base_url="http://cae.example:9000") as client:
        assert (await client.post(MCP_PATH, json=INITIALIZE, headers=HEADERS)).status_code == 200


async def test_extra_allowed_host_is_accepted(settings):
    config = settings.model_copy(update={"mcp_allowed_hosts": "192.168.16.128:8000"})
    async with running_app(config, base_url="http://192.168.16.128:8000") as client:
        assert (await client.post(MCP_PATH, json=INITIALIZE, headers=HEADERS)).status_code == 200


async def test_unknown_host_is_refused(settings):
    async with running_app(settings, base_url="http://attacker.example") as client:
        assert (await client.post(MCP_PATH, json=INITIALIZE, headers=HEADERS)).status_code == 421


async def test_disabled_flag_mounts_nothing(settings):
    config = settings.model_copy(update={"mcp_enabled": False})
    async with running_app(config) as client:
        assert (await client.post(MCP_PATH, json=INITIALIZE, headers=HEADERS)).status_code == 404
        assert (await client.get("/healthz")).status_code == 200
