"""Single-host task worker. Run separately with: python worker.py."""

import asyncio
import fcntl
import signal
import time
from contextlib import contextmanager

from loguru import logger

from core.config import Settings
from core.logging import configure_logging
from services import modal, paths
from services.cloud_png import render_cloud_from_json
from services.executor import execute
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
        started_at = time.monotonic()
        logger.info(
            "CLAIMED run_id={} status=running model={} young={} poisson={} density={} roots={}",
            run_id,
            parameters.get("model_filename"),
            parameters.get("young_modulus"),
            parameters.get("poisson_ratio"),
            parameters.get("density"),
            parameters.get("number_of_roots"),
        )
        try:
            run_dir = paths.task_dir(self.settings, run_id)
            if not run_dir.is_dir():
                raise ValueError("任务工作目录不存在或已被删除")
            parameters["run_dir"] = str(run_dir)
            parameters["test_sleep_seconds"] = str(self.settings.test_sleep_seconds)
            parameters["test_exit_code"] = str(self.settings.test_exit_code)
            logger.info(
                "START run_id={} cmd={} workdir={}",
                run_id,
                modal.command(self.settings),
                run_dir / "project",
            )
            result = await execute(
                modal.command(self.settings),
                modal.environment(parameters),
                run_dir / "project",
                run_dir / "logs",
                self.settings.run_timeout_seconds,
            )
            elapsed = time.monotonic() - started_at
            if result.timed_out:
                status, error = "timed_out", "脚本运行超时"
            elif result.exit_code == 0:
                status, error = "succeeded", None
            else:
                status, error = "failed", "脚本退出码非 0"
            logger.info(
                "DONE run_id={}, status={}, exit_code={}, elapsed={:.2f}s",
                run_id,
                status,
                result.exit_code,
                elapsed,
            )
            if status == "succeeded":
                try:
                    cloud_dir = await asyncio.to_thread(
                        render_cloud_from_json, paths.cloud_info(run_dir)
                    )
                except Exception as exc:
                    status, error = "failed", f"云图生成失败：{exc}"
                    logger.exception("run_id={} cloud generation failed", run_id)
                else:
                    if cloud_dir is not None:
                        parameters["cloud_dir"] = cloud_dir
                        logger.info("CLOUD run_id={} cloud_dir={}", run_id, cloud_dir)
                    else:
                        logger.info("CLOUD run_id={} no renderable cloud_info, skipped", run_id)
            await self.store.finish(run_id, status, result.exit_code, error)
            logger.info(
                "FINISH run_id={} status={} exit_code={} error={} elapsed={:.2f}s",
                run_id,
                status,
                result.exit_code,
                error,
                time.monotonic() - started_at,
            )
        except asyncio.CancelledError:
            await self.store.finish(run_id, "unknown", None, "Worker 被中断，需人工核对")
            logger.warning("CANCELLED run_id={} marked unknown, run_id")
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
                logger.info(
                    "Worker stared db={} workspace={} guierunner={} poll={}s timeout={}s",
                    self.settings.database_path.resolve(),
                    self.settings.workspace_root.resolve(),
                    self.settings.guierunner_path or "test-script",
                    self.settings.worker_poll_seconds,
                    self.settings.run_timeout_seconds,
                )
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
