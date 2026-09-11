import pytest

from tests.helpers import create_project, key


@pytest.mark.parametrize(
    "payload",
    [
        {"material_name": ""},
        {"material_name": "   "},
        {"material_name": "x", "density": -1},
        {"material_name": "x", "young_modulus": "2e11"},
        {"material_name": "x", "poisson_ratio": 0.5},
        {"material_name": "x", "young_modulus": True},
        {"material_name": "x", "typo": 1},
    ],
)
async def test_invalid_materials_do_not_create(client, payload):
    pid = await create_project(client)
    result = await client.post(f"/api/v1/projects/{pid}/materials", json=payload, headers=key())
    assert result.status_code == 422, result.text
    assert result.headers["content-type"].startswith("application/problem+json")
    project = (await client.get(f"/api/v1/projects/{pid}")).json()["data"]
    assert project["materials"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("mesh_option", "periodic_boundary"),
        ("advanced_option_enable", True),
        ("extensions", {"jusmar_app": {"hidden": 1}}),
    ],
)
async def test_unsupported_mesh_settings(client, field, value):
    pid = await create_project(client)
    result = await client.post(f"/api/v1/projects/{pid}/meshes", json={field: value}, headers=key())
    assert result.status_code == 422
    assert result.json()["code"] == "UNSUPPORTED_PARAMETER"


async def test_missing_idempotency_and_invalid_bool(client):
    result = await client.post("/api/v1/projects/missing/load-cases", json={})
    assert result.status_code == 422
    result = await client.post(
        "/api/v1/projects/missing/load-cases", json={"add_load": "false"}, headers=key()
    )
    assert result.status_code == 422


async def test_object_temperatures_not_silently_ignored(client):
    result = await client.put(
        "/api/v1/projects/missing/case-settings",
        headers=key(),
        json={"selected_load_case": "case", "material_property_temp": {"value": 10}},
    )
    assert result.status_code == 422
    assert result.json()["code"] == "UNSUPPORTED_PARAMETER"
