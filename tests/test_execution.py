import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.providers.mock import MockAdapter
from tests.helpers import create_project, key
from tests.legacy_client import WorkflowError, run_workflow, wait_for


async def test_mesh_reserves_project_and_replay_returns_same_operation(client, app):
    pid = await create_project(client)
    app.state.container.shared.provider.delay = 0.1
    headers = key()
    first = await client.post(f"/api/v1/projects/{pid}/meshes", json={}, headers=headers)
    replay = await client.post(f"/api/v1/projects/{pid}/meshes", json={}, headers=headers)
    assert first.status_code == replay.status_code == 202
    assert first.json()["data"] == replay.json()["data"]
    assert first.headers["location"] == first.json()["data"]["status_url"]
    conflict = await client.post(
        f"/api/v1/projects/{pid}/materials", json={"material_name": "x"}, headers=key()
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "PROJECT_BUSY"
    await wait_for(client, first.headers["location"])


async def test_failed_operation_is_queryable_and_can_be_corrected(client, app):
    pid = await create_project(client)
    provider = app.state.container.shared.provider
    provider.failures.add("generate_mesh")
    response = await client.post(f"/api/v1/projects/{pid}/meshes", json={}, headers=key())
    with pytest.raises(WorkflowError):
        await wait_for(client, response.headers["location"])
    result = await client.get(response.headers["location"])
    assert result.status_code == 200
    assert result.json()["data"]["status"] == "failed"
    assert result.json()["data"]["error"]["code"] == "MOCK_EXECUTION_FAILED"
    assert (await client.get(f"/api/v1/projects/{pid}")).json()["data"]["mesh"] is None
    provider.failures.clear()
    retry = await client.post(f"/api/v1/projects/{pid}/meshes", json={}, headers=key())
    await wait_for(client, retry.headers["location"])


async def test_task_snapshot_is_immutable_and_submission_idempotent(client):
    completed = await run_workflow(client, b"mock")
    pid, case_id = completed["project_id"], completed["load_case_id"]
    headers = key()
    path = f"/api/v1/projects/{pid}/simulation-tasks"
    payload = {"analyze_load_case_list": [case_id]}
    responses = await asyncio.gather(
        *[client.post(path, json=payload, headers=headers) for _ in range(3)]
    )
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["data"]["task_id"] for r in responses}) == 1
    task_url = responses[0].headers["location"]
    task = await wait_for(client, task_url)
    revision = task["input_snapshot"]["revision"]
    update = await client.post(
        f"/api/v1/projects/{pid}/materials", json={"material_name": "later"}, headers=key()
    )
    assert update.status_code == 201
    assert (await client.get(task_url)).json()["data"]["input_snapshot"]["revision"] == revision
    assert (await client.get(f"/api/v1/projects/{pid}")).json()["data"]["revision"] > revision


async def test_reimport_invalidates_geometry_dependents(client):
    completed = await run_workflow(client, b"first")
    pid = completed["project_id"]
    upload = await client.post(
        "/api/v1/files", files={"file": ("new.stp", b"second")}, headers=key()
    )
    response = await client.post(
        "/api/v1/geometry-imports",
        headers=key(),
        json={
            "project_id": pid,
            "source": {"type": "file_id", "file_id": upload.json()["data"]["file_id"]},
        },
    )
    await wait_for(client, response.headers["location"])
    view = (await client.get(f"/api/v1/projects/{pid}")).json()["data"]
    assert view["geometry"]["geometry_revision"] == 2
    assert view["mesh"] is None
    assert view["properties"] == []
    assert view["case_settings"] is None
    assert view["materials"] and view["task_ids"]
    result = await client.post(
        f"/api/v1/projects/{pid}/simulation-tasks",
        headers=key(),
        json={"analyze_load_case_list": [completed["load_case_id"]]},
    )
    assert result.status_code == 409


async def test_solver_failure_is_not_reported_as_success(settings):
    app = create_app(settings, MockAdapter(0.001, failures={"simulation_result"}))
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer alpha-token"},
        ) as client:
            with pytest.raises(WorkflowError, match="MOCK_SOLVER_FAILED"):
                await run_workflow(client, b"mock")


async def test_real_provider_fails_startup(settings):
    app = create_app(settings.model_copy(update={"provider": "jusmar"}))
    with pytest.raises(RuntimeError, match="真实 SDK"):
        async with app.router.lifespan_context(app):
            pytest.fail("must not start with real provider")


async def test_other_owner_cannot_query_execution(client):
    result = await run_workflow(client, b"mock")
    query = await client.get(
        f"/api/v1/simulation-tasks/{result['task_id']}",
        headers={"Authorization": "Bearer beta-token"},
    )
    assert query.status_code == 404
