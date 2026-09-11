"""Fixed Mock entry point launched by the Worker, not submitted by an Agent."""

import argparse
import asyncio
import hashlib
import platform
from pathlib import Path

from app.execution.protocol import JobInput, JobOutput, JobProgress, atomic_json, read_manifest
from app.execution.runner import execution_error
from app.providers.mock import MockAdapter
from app.schemas.analysis import StepView
from app.workflows.registry import get_workflow


async def execute(directory: Path) -> int:
    command = JobInput.model_validate(read_manifest(directory / "input.json"))
    definition = get_workflow(command.request.workflow_id, command.request.workflow_version)
    steps = [StepView(name=name, status="pending") for name in definition.steps]

    def report():
        atomic_json(
            directory / "progress.json",
            JobProgress(run_id=command.run_id, attempt_id=command.attempt_id, steps=steps),
        )

    common = dict(
        run_id=command.run_id,
        attempt_id=command.attempt_id,
        implementation_version=definition.implementation_version,
        runtime_versions={
            "python": platform.python_version(),
            "runner": "mock-entry-1",
            "provider": "stateful-mock-1",
            "cae_software": "not_installed",
        },
    )
    try:
        if command.implementation_version != definition.implementation_version:
            raise ValueError("workflow implementation version mismatch")
        actual_hash = hashlib.sha256((directory / command.geometry_path).read_bytes()).hexdigest()
        if actual_hash != command.geometry_sha256:
            raise ValueError("staged geometry checksum mismatch")
        provider = MockAdapter(command.mock_delay_seconds, set(command.mock_failures))
        result = await definition.execute(provider, command, directory, steps, report)
        output = JobOutput(**common, status="succeeded", steps=steps, result=result)
    except Exception as exc:
        output = JobOutput(**common, status="failed", steps=steps, error=execution_error(exc))
    atomic_json(directory / "result.json", output)
    return 0 if output.status == "succeeded" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(execute(args.job_dir.resolve())))


if __name__ == "__main__":
    main()
