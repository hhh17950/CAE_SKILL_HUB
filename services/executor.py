"""POSIX subprocess execution with file-backed output and process-group cleanup."""

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    timed_out: bool = False


async def kill_group(process: asyncio.subprocess.Process):
    # Also terminate children when the parent already exited during a race.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def execute(
    command: list[str], env: dict[str, str], cwd: Path, log_dir: Path, timeout: float
) -> ExecutionResult:
    with (
        (log_dir / "stdout.log").open("wb") as stdout,
        (log_dir / "stderr.log").open("wb") as stderr,
    ):
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            code = await asyncio.wait_for(process.wait(), timeout)
            return ExecutionResult(code)
        except asyncio.TimeoutError:
            await kill_group(process)
            return ExecutionResult(process.returncode, timed_out=True)
        except BaseException:
            await kill_group(process)
            raise
