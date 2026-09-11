from uuid import uuid4

from tests.legacy_client import wait_for


def key(value=None):
    return {"Idempotency-Key": value or uuid4().hex}


async def create_project(client):
    uploaded = await client.post(
        "/api/v1/files", files={"file": ("test.stp", b"mock CAD bytes")}, headers=key()
    )
    assert uploaded.status_code == 201, uploaded.text
    result = await client.post(
        "/api/v1/geometry-imports",
        headers=key(),
        json={"source": {"type": "file_id", "file_id": uploaded.json()["data"]["file_id"]}},
    )
    assert result.status_code == 202, result.text
    operation = await wait_for(client, result.json()["data"]["status_url"])
    return operation["result"]["project_id"]
