"""Nine-operation regression client for the opt-in legacy test surface only."""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx


class WorkflowError(RuntimeError):
    pass


async def wait_for(client: httpx.AsyncClient, url: str, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(url)
        response.raise_for_status()
        body = response.json()
        if body["execution_mode"] != "mock":
            raise WorkflowError("This test client only permits mock execution")
        result = body["data"]
        if result["status"] == "succeeded":
            return result
        if result["status"] not in {"queued", "running"}:
            raise WorkflowError(
                f"Execution did not succeed: {json.dumps(result, ensure_ascii=False)}"
            )
        await asyncio.sleep(0.01)
    raise WorkflowError(f"Polling timed out; inspect existing execution at {url}; do not resubmit")


async def run_workflow(
    client: httpx.AsyncClient, geometry: bytes, filename: str = "mock_geometry.stp"
) -> dict:
    run_id = uuid4().hex
    steps = []

    async def invoke(method: str, path: str, expected: int, payload=None, files=None):
        response = await client.request(
            method,
            "/api/v1" + path,
            json=payload,
            files=files,
            headers={"Idempotency-Key": f"{run_id}-{len(steps)}"},
        )
        if response.status_code != expected:
            raise WorkflowError(f"{method} {path}: {response.status_code} {response.text}")
        body = response.json()
        if body["execution_mode"] != "mock":
            raise WorkflowError("Unexpected non-mock execution")
        steps.append(
            {"path": path, "status_code": response.status_code, "request_id": body["request_id"]}
        )
        return body["data"]

    capabilities = await client.get("/api/v1/capabilities")
    capabilities.raise_for_status()
    if capabilities.json()["data"]["execution_mode"] != "mock":
        raise WorkflowError("Refusing to run this demo against a real provider")

    uploaded = await invoke("POST", "/files", 201, files={"file": (filename, geometry)})
    imported = await invoke(
        "POST",
        "/geometry-imports",
        202,
        {"source": {"type": "file_id", "file_id": uploaded["file_id"]}},
    )
    imported_result = await wait_for(client, imported["status_url"])
    project_id = imported_result["result"]["project_id"]
    prefix = f"/projects/{project_id}"

    mesh = await invoke("POST", prefix + "/meshes", 202, {"mesh_density": "medium"})
    await wait_for(client, mesh["status_url"])
    material = await invoke("POST", prefix + "/materials", 201, {"material_name": "测试钢材"})
    property_result = await invoke(
        "POST", prefix + "/properties/3d", 201, {"material_ref": material["material_id"]}
    )
    load_case = await invoke("POST", prefix + "/load-cases", 201, {})
    await invoke(
        "PUT",
        prefix + "/solver-settings",
        200,
        {"solver_type": "mock_modal", "linear_solver_type": "mock_direct"},
    )
    await invoke(
        "PUT", prefix + "/case-settings", 200, {"selected_load_case": load_case["load_case_id"]}
    )
    await invoke("PUT", prefix + "/post-processing-settings", 200, {})
    submitted = await invoke(
        "POST",
        prefix + "/simulation-tasks",
        202,
        {"analyze_load_case_list": [load_case["load_case_id"]]},
    )
    task = await wait_for(client, submitted["status_url"])

    response = await client.get("/api/v1" + prefix)
    response.raise_for_status()
    project = response.json()["data"]
    checks = {
        "project_id": task["project_id"] == project_id,
        "material_default": project["materials"][0]["parameters"]["young_modulus"] == 2e11,
        "property_binding": project["properties"][0]["parameters"]["material_ref"]
        == material["material_id"],
        "load_case_binding": project["case_settings"]["selected_load_case"]
        == load_case["load_case_id"],
        "mesh_revision": project["mesh"]["geometry_revision"]
        == project["geometry"]["geometry_revision"],
        "task_snapshot": task["input_snapshot"]["revision"] == project["revision"],
        "analysis_count": task["input_snapshot"]["case_settings"]["eigen_count_value"] == 10,
        "submission_cases": task["parameters"]["analyze_load_case_list"]
        == [load_case["load_case_id"]],
    }
    if not all(checks.values()):
        raise WorkflowError(f"Contract assertions failed: {checks}")
    return {
        "execution_mode": "mock",
        "passed": True,
        "business_steps": 9,
        "project_id": project_id,
        "material_id": material["material_id"],
        "property_id": property_result["property_id"],
        "load_case_id": load_case["load_case_id"],
        "task_id": task["task_id"],
        "task_status": task["status"],
        "checks": checks,
        "requests": steps,
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--geometry", type=Path, default=Path(__file__).parent / "fixtures/mock_geometry.stp"
    )
    args = parser.parse_args()
    token = os.environ.get("CAE_CLIENT_TOKEN", "local-test-token")
    async with httpx.AsyncClient(
        base_url=args.base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
        trust_env=False,
    ) as client:
        result = await run_workflow(client, args.geometry.read_bytes(), args.geometry.name)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
