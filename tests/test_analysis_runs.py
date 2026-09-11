import asyncio
import json
import time
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from app.execution.protocol import read_manifest
from app.execution.worker import Worker, verified_output
from app.main import create_app
from app.storage.jobs import JobRepository
from examples.modal_workflow import modal_request, run_workflow


@pytest.fixture
def workflow_settings(settings):
    return settings.model_copy(update={"enable_legacy_api": False, "worker_poll_seconds": 0.02})


@asynccontextmanager
async def connect(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer alpha-token"},
        ) as client:
            yield client


async def upload(client, content=b"Mock geometry"):
    response = await client.post("/api/v1/assets", files={"file": ("part.stp", content)})
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def submit(client, payload=None, key="analysis-key"):
    if payload is None:
        payload = modal_request((await upload(client))["asset_id"])
    response = await client.post(
        "/api/v1/analysis-runs", json=payload, headers={"Idempotency-Key": key}
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


async def test_workflow_http_client_and_isolated_worker(workflow_settings):
    async with connect(workflow_settings) as client:
        worker = Worker(workflow_settings)
        task = asyncio.create_task(worker.serve())
        try:
            result = await run_workflow(client, b"Mock geometry")
            assert result["passed"] and all(result["checks"].values())
            assert (await client.post("/api/v1/geometry-imports", json={})).status_code == 404
        finally:
            worker.stop.set()
            await task


async def test_restart_preserves_asset_queue_result_and_idempotency(workflow_settings):
    async with connect(workflow_settings) as client:
        asset = await upload(client)
        payload = modal_request(asset["asset_id"])
        first = await submit(client, payload)
    async with connect(workflow_settings) as client:
        replay = await submit(client, payload)
        assert replay["run_id"] == first["run_id"]
        assert replay["status"] == "queued"
        assert (await client.get(f"/api/v1/assets/{asset['asset_id']}")).status_code == 200
        assert await Worker(workflow_settings).run_once()
    async with connect(workflow_settings) as client:
        replay = await submit(client, payload)
        assert replay["run_id"] == first["run_id"] and replay["status"] == "succeeded"
        run = (await client.get(first["status_url"])).json()["data"]
        assert run["runtime_versions"]["runner"] == "mock-entry-1"
        assert run["request"]["input"]["mesh"]["element_order"] == "first_order"
        assert run["result"]["requested_mode_count"] == 10


async def test_concurrent_submit_and_atomic_claim(workflow_settings):
    async with connect(workflow_settings) as client:
        payload = modal_request((await upload(client))["asset_id"])
        replies = await asyncio.gather(*[submit(client, payload) for _ in range(5)])
        assert len({r["run_id"] for r in replies}) == 1
        repo = JobRepository(workflow_settings.database_path)
        claims = await asyncio.gather(repo.claim("first", 10), repo.claim("second", 10))
        assert sum(c is not None for c in claims) == 1
        payload["input"]["mode_count"] = 3
        response = await client.post(
            "/api/v1/analysis-runs", json=payload, headers={"Idempotency-Key": "analysis-key"}
        )
        assert response.status_code == 409
        assert response.json()["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize(
    "change",
    [
        {"boundary_condition": "fixed"},
        {"loads": []},
        {"process_count": True},
        {"mode_count": "10"},
        {"mode_count": 0},
        {"extensions": {"sdk": {}}},
    ],
)
async def test_strict_workflow_contract(workflow_settings, change):
    async with connect(workflow_settings) as client:
        payload = modal_request((await upload(client))["asset_id"])
        payload["input"].update(change)
        response = await client.post("/api/v1/analysis-validations", json=payload)
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"


async def test_preflight_deferred_checks_and_material_units(workflow_settings):
    async with connect(workflow_settings) as client:
        payload = modal_request((await upload(client))["asset_id"])
        report = (await client.post("/api/v1/analysis-validations", json=payload)).json()["data"]
        assert report["valid"]
        assert {c["name"] for c in report["checks"] if c["status"] == "deferred"} == {
            "worker_environment",
            "cae_model",
        }
        payload["input"]["process_count"] = 64
        assert (await client.post("/api/v1/analysis-validations", json=payload)).json()["data"][
            "valid"
        ] is False
        assert (
            await client.post(
                "/api/v1/analysis-runs", json=payload, headers={"Idempotency-Key": "limit"}
            )
        ).status_code == 422
        del payload["input"]["material"]["young_modulus_pa"]
        assert (await client.post("/api/v1/analysis-validations", json=payload)).status_code == 422


async def test_ownership_applies_to_assets_runs_cancel_and_results(workflow_settings):
    async with connect(workflow_settings) as client:
        asset = await upload(client)
        payload = modal_request(asset["asset_id"])
        run = await submit(client, payload)
        assert await Worker(workflow_settings).run_once()
        other = {"Authorization": "Bearer beta-token"}
        for suffix in ("", "/results", "/artifacts/summary"):
            assert (await client.get(run["status_url"] + suffix, headers=other)).status_code == 404
        assert (await client.post(run["status_url"] + "/cancel", headers=other)).status_code == 404
        assert (
            await client.post("/api/v1/analysis-validations", json=payload, headers=other)
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/assets/{asset['asset_id']}", headers=other)
        ).status_code == 404


async def test_queued_cancel_never_launches(workflow_settings):
    async with connect(workflow_settings) as client:
        run = await submit(client)
        response = await client.post(run["status_url"] + "/cancel")
        assert response.status_code == 200 and response.json()["data"]["status"] == "cancelled"
        assert not await Worker(workflow_settings).run_once()
        assert (await client.get(run["status_url"] + "/results")).status_code == 409


async def test_running_cancel_confirms_mock_process_stop(workflow_settings):
    config = workflow_settings.model_copy(update={"mock_delay_seconds": 1})
    async with connect(config) as client:
        run = await submit(client)
        work = asyncio.create_task(Worker(config).run_once())
        for _ in range(200):
            view = (await client.get(run["status_url"])).json()["data"]
            if view["current_step"]:
                break
            await asyncio.sleep(0.02)
        assert view["current_step"] == "import_geometry"
        assert (await client.post(run["status_url"] + "/cancel")).status_code == 202
        await work
        view = (await client.get(run["status_url"])).json()["data"]
        assert view["status"] == "cancelled" and view["result"] is None


async def test_timeout_and_solver_failure_never_succeed(workflow_settings):
    async with connect(workflow_settings) as client:
        failed = await submit(client, key="solver-fails")
        await Worker(workflow_settings, failures=["simulation_result"]).run_once()
        view = (await client.get(failed["status_url"])).json()["data"]
        assert view["status"] == "failed" and view["result"] is None
        assert view["error"]["code"] == "MOCK_SOLVER_FAILED"
        assert (
            next(s for s in view["steps"] if s["name"] == "submit_simulation")["status"]
            == "succeeded"
        )
        timed = await submit(client, key="times-out")
        config = workflow_settings.model_copy(
            update={"mock_delay_seconds": 2, "run_timeout_seconds": 1}
        )
        await Worker(config).run_once()
        assert (await client.get(timed["status_url"])).json()["data"]["status"] == "timed_out"


async def test_expired_lease_quarantines_and_fences_old_worker(workflow_settings):
    async with connect(workflow_settings) as client:
        first = await submit(client, key="first")
        second = await submit(client, key="second")
        repo = JobRepository(workflow_settings.database_path)
        _, claimed = await repo.claim("old-worker", 10)
        async with repo.connection(write=True) as db:
            await db.execute(
                "UPDATE analysis_runs SET lease_until=? WHERE run_id=?",
                (time.time() - 1, claimed.run_id),
            )
        assert await repo.claim("new-worker", 10) is None
        assert (await client.get(first["status_url"])).json()["data"]["status"] == "unknown"
        assert not await repo.finish(claimed.run_id, claimed.attempt_id, "succeeded")
        assert (await client.post(first["status_url"] + "/cancel")).status_code == 409
        await repo.resolve_unknown(claimed.run_id)
        assert (await repo.claim("new-worker", 10))[1].run_id == second["run_id"]


async def test_tampered_asset_fails_before_start(workflow_settings):
    async with connect(workflow_settings) as client:
        asset = await upload(client)
        run = await submit(client, modal_request(asset["asset_id"]))
        record = await JobRepository(workflow_settings.database_path).asset(
            "alpha", asset["asset_id"]
        )
        record.path.write_bytes(b"changed")
        await Worker(workflow_settings).run_once()
        view = (await client.get(run["status_url"])).json()["data"]
        assert view["error"]["code"] == "ASSET_INTEGRITY_ERROR"
        assert all(s["status"] == "pending" for s in view["steps"])


async def test_output_identity_exit_code_and_artifact_integrity(workflow_settings):
    async with connect(workflow_settings) as client:
        run = await submit(client)
        await Worker(workflow_settings).run_once()
        view = await JobRepository(workflow_settings.database_path).run("alpha", run["run_id"])
        directory = workflow_settings.job_root / view.run_id / view.attempt_id
        assert verified_output(directory, view, 0).status == "succeeded"
        with pytest.raises(ValueError):
            verified_output(directory, view, 1)
        manifest = read_manifest(directory / "result.json")
        manifest["attempt_id"] = "different-attempt"
        (directory / "result.json").write_text(json.dumps(manifest))
        with pytest.raises(ValueError):
            verified_output(directory, view, 0)
        (directory / "summary.json").write_text("changed")
        response = await client.get(run["status_url"] + "/artifacts/summary")
        assert response.status_code == 500 and response.json()["code"] == "ARTIFACT_INTEGRITY_ERROR"


async def test_catalog_and_openapi_publish_actual_typed_contract(workflow_settings):
    async with connect(workflow_settings) as client:
        descriptions = (await client.get("/api/v1/workflows")).json()["data"]
        assert len(descriptions) == 1 and descriptions[0]["execution_mode"] == "mock"
        assert descriptions[0]["input_schema"]["additionalProperties"] is False
        assert (
            await client.get("/api/v1/workflows/modal_analysis/versions/2.0")
        ).status_code == 404
        schema = (await client.get("/openapi.json")).json()
        assert "/api/v1/geometry-imports" not in schema["paths"]
        ref = schema["paths"]["/api/v1/analysis-runs"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        assert ref.endswith("ModalAnalysisRequest")
        assert schema["components"]["schemas"]["ModalInput"]["additionalProperties"] is False
        assert schema["components"]["schemas"]["MockSummary"]["additionalProperties"] is False


async def test_workflow_passes_actual_intermediate_ids_to_sdk(tmp_path):
    from app.execution.protocol import JobInput
    from app.providers.mock import MockAdapter
    from app.schemas.analysis import AnalysisRequest, StepView
    from app.workflows.registry import MODAL

    request = AnalysisRequest.model_validate(modal_request("asset_fixture"))
    command = JobInput(
        run_id="run_fixture",
        attempt_id="attempt_fixture",
        request=request,
        implementation_version=MODAL.implementation_version,
        geometry_sha256="0" * 64,
        geometry_suffix=".stp",
        mock_delay_seconds=0,
    )
    provider = MockAdapter(0)
    steps = [StepView(name=name, status="pending") for name in MODAL.steps]
    result = await MODAL.execute(provider, command, tmp_path, steps, lambda: None)
    calls = {call["operation"]: call["parameters"] for call in provider.history[result.project_id]}
    assert len(calls) == 9
    assert calls["create_3d_property"]["material_ref"] == result.material_id
    assert calls["case_settings"]["selected_load_case"] == result.load_case_id
    assert calls["submit_simulation"]["analyze_load_case_list"] == [result.load_case_id]
    assert calls["create_material"]["young_modulus"] == request.input.material.young_modulus_pa
    assert calls["add_load_case"]["add_constraint"] is False
