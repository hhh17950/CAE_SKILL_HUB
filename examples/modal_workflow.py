"""HTTP-only client simulating a partner Agent. Start API and Worker first."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx


def modal_request(asset_id: str) -> dict:
    return {
        "workflow_id": "modal_analysis",
        "workflow_version": "1.0",
        "input": {
            "geometry": {"geometry_asset_id": asset_id, "length_unit": "mm"},
            "material": {
                "material_name": "示例钢材",
                "young_modulus_pa": 2e11,
                "poisson_ratio": 0.3,
                "density_kg_m3": 7850,
            },
            "mesh": {"mesh_density": "medium"},
            "boundary_condition": "free",
            "mode_count": 10,
            "process_count": 1,
        },
    }


def data(response: httpx.Response) -> dict:
    response.raise_for_status()
    body = response.json()
    if body.get("execution_mode") != "mock":
        raise RuntimeError("This example only permits Mock execution")
    return body["data"]


async def wait_for(client: httpx.AsyncClient, url: str, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = data(await client.get(url))
        if run["status"] == "succeeded":
            return run
        if run["status"] not in {"queued", "running", "cancelling"}:
            raise RuntimeError(f"Analysis failed: {json.dumps(run['error'], ensure_ascii=False)}")
        await asyncio.sleep(0.1)
    raise RuntimeError(f"等待超时，请查询 {url} 并检查独立 Worker；不要重新提交新任务")


async def run_workflow(
    client: httpx.AsyncClient, geometry: bytes, filename="mock_geometry.stp"
) -> dict:
    asset = data(await client.post("/api/v1/assets", files={"file": (filename, geometry)}))
    payload = modal_request(asset["asset_id"])
    validation = data(await client.post("/api/v1/analysis-validations", json=payload))
    if not validation["valid"]:
        raise RuntimeError(f"预检失败: {validation['checks']}")
    headers = {"Idempotency-Key": uuid4().hex}
    response = await client.post("/api/v1/analysis-runs", json=payload, headers=headers)
    accepted = data(response)
    replay = data(await client.post("/api/v1/analysis-runs", json=payload, headers=headers))
    run = await wait_for(client, accepted["status_url"])
    result = data(await client.get(accepted["status_url"] + "/results"))
    artifact = result["artifacts"][0]
    download = await client.get(artifact["download_url"])
    download.raise_for_status()
    checks = {
        "same_run_on_retry": replay["run_id"] == accepted["run_id"],
        "all_11_steps_completed": len(run["steps"]) == 11
        and all(s["status"] == "succeeded" for s in run["steps"]),
        "result_is_mock": result["synthetic"] is True
        and result["quality_status"] == "not_evaluated",
        "input_hash_preserved": run["input_asset_sha256"] == hashlib.sha256(geometry).hexdigest(),
        "artifact_hash_verified": artifact["sha256"]
        == hashlib.sha256(download.content).hexdigest(),
        "material_binding": download.json()["material_id"] == result["material_id"],
    }
    if not all(checks.values()):
        raise RuntimeError(f"合同断言失败: {checks}")
    return {
        "passed": True,
        "execution_mode": "mock",
        "run_id": run["run_id"],
        "status": run["status"],
        "checks": checks,
        "notice": result["notice"],
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
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
