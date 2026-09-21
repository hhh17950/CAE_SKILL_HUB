"""Single-host task worker. Run separately with: python worker.py."""

import asyncio
import fcntl
import signal
from contextlib import contextmanager
from pathlib import Path

from loguru import logger

from core.config import Settings
from core.logging import configure_logging
from services import modal
from services.executor import execute
from services.paths import existing_project_dir, new_output_file
from storage.repository import RunRepository


@contextmanager
def worker_lock(database_path):
    """Kernel lock: released on exit, including crashes; the lock file may remain."""
    path = database_path.resolve().with_suffix(".worker.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("该数据库已有 Worker 运行，请勿重复启动") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class GuieWorker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = RunRepository(settings.database_path)

    async def run_once(self) -> bool:
        """Execute one queued task. Production callers must hold worker_lock."""
        claimed = await self.store.claim()
        if claimed is None:
            return False
        run_id, parameters = claimed
        try:
            run_dir = self.settings.run_root.resolve() / run_id
            log_dir = self.settings.task_log_root.resolve() / run_id
            run_dir.mkdir(parents=True, exist_ok=False)
            log_dir.mkdir(parents=True, exist_ok=False)
            parameters["project_dir"] = str(
                existing_project_dir(parameters["project_dir"], self.settings)
            )
            for name in ("jusmar_log_path", "cloud_info_path"):
                if value := parameters[name]:
                    parameters[name] = str(new_output_file(value, self.settings, name))
            # Re-check in the worker: a queued input file may have changed since submission.
            model = Path(parameters["model_path"]).resolve()
            root = self.settings.model_root
            if root is None or not model.is_relative_to(root.resolve()) or not model.is_file():
                raise ValueError("模型文件不存在或超出允许目录")
            parameters["model_path"] = str(model)
            result = await execute(
                modal.command(),
                modal.environment(parameters, run_dir, log_dir, self.settings),
                run_dir,
                log_dir,
                self.settings.run_timeout_seconds,
            )
            if result.timed_out:
                status, error = "timed_out", "脚本运行超时"
            elif result.exit_code == 0:
                status, error = "succeeded", None
            else:
                status, error = "failed", "脚本退出码非 0"
            await self.store.finish(run_id, status, result.exit_code, error)
            logger.info("run_id={} status={} exit_code={}", run_id, status, result.exit_code)
        except asyncio.CancelledError:
            await self.store.finish(run_id, "unknown", None, "Worker 被中断，需人工核对")
            raise
        except Exception:
            logger.exception("run_id={} execution error", run_id)
            await self.store.finish(run_id, "failed", None, "执行器异常，请检查 Worker 日志")
        return True

    async def serve(self):
        try:
            await self.store.initialize()
            with worker_lock(self.settings.database_path):
                await self.store.recover_running()
                logger.info("modal test-script worker started")
                while True:
                    if not await self.run_once():
                        await asyncio.sleep(self.settings.worker_poll_seconds)
        finally:
            await self.store.close()


async def main():
    settings = Settings()
    configure_logging(settings.service_log_root, "worker")
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await GuieWorker(settings).serve()
    except asyncio.CancelledError:
        logger.info("Worker stopped")
    finally:
        await logger.complete()


if __name__ == "__main__":
    asyncio.run(main())
