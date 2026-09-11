"""Start a real local HTTP server and a separate Worker in an isolated test directory."""

import argparse
import asyncio
import json
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx

from examples.modal_workflow import run_workflow


async def stop(process):
    if process and process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            process.kill()
            await process.wait()


async def ready(client, process):
    for _ in range(200):
        if process.returncode is not None:
            raise RuntimeError("API process exited before becoming ready")
        try:
            if (await client.get("/healthz")).status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.05)
    raise RuntimeError("API readiness timed out")


async def acceptance() -> dict:
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    api = worker = None
    with TemporaryDirectory(prefix="cae-workflow-acceptance-") as temporary:
        directory = Path(temporary)
        token = uuid4().hex
        environment = {
            **os.environ,
            "CAE_PROVIDER": "mock",
            "CAE_ENABLE_LEGACY_API": "false",
            "CAE_DATABASE_PATH": str(directory / "cae.db"),
            "CAE_ASSET_ROOT": str(directory / "assets"),
            "CAE_JOB_ROOT": str(directory / "jobs"),
            "CAE_API_TOKENS": json.dumps({token: "acceptance"}),
            "CAE_MOCK_DELAY_SECONDS": "0.03",
            "CAE_WORKER_POLL_SECONDS": "0.05",
            "CAE_WORKER_LEASE_SECONDS": "15",
            "CAE_RUN_TIMEOUT_SECONDS": "30",
        }
        with (directory / "processes.log").open("wb") as log:

            async def start_api():
                return await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    cwd=root,
                    env=environment,
                    stdout=log,
                    stderr=log,
                )

            try:
                api = await start_api()
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}",
                    headers={"Authorization": f"Bearer {token}"},
                    trust_env=False,
                    timeout=10,
                ) as client:
                    await ready(client, api)
                    worker = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "app.execution.worker",
                        cwd=root,
                        env=environment,
                        stdout=log,
                        stderr=log,
                    )
                    report = await run_workflow(client, b"Mock-only acceptance geometry")
                    await stop(api)
                    api = await start_api()
                    await ready(client, api)
                    response = await client.get(f"/api/v1/analysis-runs/{report['run_id']}")
                    response.raise_for_status()
                    run = response.json()["data"]
                    report["checks"]["result_survives_api_process_restart"] = (
                        run["status"] == "succeeded"
                    )
                    artifact = await client.get(run["result"]["artifacts"][0]["download_url"])
                    artifact.raise_for_status()
                    report["checks"]["artifact_survives_api_process_restart"] = (
                        artifact.json()["run_id"] == report["run_id"]
                    )
                    if not all(report["checks"].values()):
                        raise RuntimeError("Acceptance assertions failed")
                    report["verified_at"] = datetime.now(UTC).isoformat()
                    report["implementation_version"] = run["implementation_version"]
                    report["runtime_versions"] = run["runtime_versions"]
                    report["transport"] = "real_loopback_http"
                    return report
            except Exception:
                log.flush()
                print((directory / "processes.log").read_text(errors="replace"), file=sys.stderr)
                raise
            finally:
                await stop(worker)
                await stop(api)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = asyncio.run(acceptance())
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
