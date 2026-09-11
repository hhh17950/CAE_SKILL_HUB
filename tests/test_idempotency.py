import asyncio

from tests.helpers import create_project, key


async def test_concurrent_replays_create_one_material(client, app):
    pid = await create_project(client)
    headers = key()
    responses = await asyncio.gather(
        *[
            client.post(
                f"/api/v1/projects/{pid}/materials", json={"material_name": "x"}, headers=headers
            )
            for _ in range(5)
        ]
    )
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["data"]["material_id"] for r in responses}) == 1
    project = (await client.get(f"/api/v1/projects/{pid}")).json()["data"]
    assert len(project["materials"]) == 1
    calls = app.state.container.shared.provider.history[pid]
    assert len([c for c in calls if c["operation"] == "create_material"]) == 1


async def test_different_payload_same_key_conflicts(client):
    pid = await create_project(client)
    headers = key()
    first = await client.post(
        f"/api/v1/projects/{pid}/materials", json={"material_name": "x"}, headers=headers
    )
    second = await client.post(
        f"/api/v1/projects/{pid}/materials", json={"material_name": "y"}, headers=headers
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


async def test_default_and_explicit_default_same_request(client):
    pid = await create_project(client)
    headers = key()
    a = await client.post(
        f"/api/v1/projects/{pid}/materials", json={"material_name": "x"}, headers=headers
    )
    b = await client.post(
        f"/api/v1/projects/{pid}/materials",
        json={"material_name": "x", "density": 7850},
        headers=headers,
    )
    assert a.json()["data"] == b.json()["data"]
