"""One Loguru configuration for API and Worker; each process owns its daily file."""

import logging
import sys
from pathlib import Path

from loguru import logger


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        logger.opt(exception=record.exc_info, depth=6).log(record.levelname, record.getMessage())


def configure_logging(root: Path, role: str):
    root.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level="INFO", enqueue=True)
    logger.add(
        root / f"{role}-{{time:YYYY-MM-DD}}.log",
        rotation="00:00",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
        level="INFO",
    )
    handler = InterceptHandler()
    logging.basicConfig(handlers=[handler], level=logging.INFO, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = [handler]
        logging.getLogger(name).propagate = False
