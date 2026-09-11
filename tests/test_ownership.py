from tests.helpers import create_project, key


async def test_authentication_required(client):
    response = await client.get("/api/v1/capabilities", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_other_owner_cannot_read_or_modify_project(client):
    pid = await create_project(client)
    headers = {"Authorization": "Bearer beta-token", **key()}
    assert (await client.get(f"/api/v1/projects/{pid}", headers=headers)).status_code == 404
    assert (
        await client.post(
            f"/api/v1/projects/{pid}/materials", headers=headers, json={"material_name": "x"}
        )
    ).status_code == 404


async def test_cross_project_material_and_load_case_rejected(client):
    first, second = await create_project(client), await create_project(client)
    material = await client.post(
        f"/api/v1/projects/{first}/materials", json={"material_name": "x"}, headers=key()
    )
    mid = material.json()["data"]["material_id"]
    result = await client.post(
        f"/api/v1/projects/{second}/properties/3d", json={"material_ref": mid}, headers=key()
    )
    assert result.status_code == 409
    load = await client.post(f"/api/v1/projects/{first}/load-cases", json={}, headers=key())
    result = await client.put(
        f"/api/v1/projects/{second}/case-settings",
        headers=key(),
        json={"selected_load_case": load.json()["data"]["load_case_id"]},
    )
    assert result.status_code == 409


async def test_other_owner_file_reference_rejected(client):
    upload = await client.post("/api/v1/files", files={"file": ("a.stp", b"test")}, headers=key())
    response = await client.post(
        "/api/v1/geometry-imports",
        json={"source": {"type": "file_id", "file_id": upload.json()["data"]["file_id"]}},
        headers={"Authorization": "Bearer beta-token", **key()},
    )
    assert response.status_code == 404
