"""Fixed stand-in for the colleague's guie2 entry. Default runtime: 60 seconds."""

import json
import os
import sys
import time
from pathlib import Path

from loguru import logger

REQUIRED = (
    "GUIE_PROJECT_DIR",
    "GUIE_JUSMAR_LOG",
    "GUIE_CLOUD_INFO_DIR",
    "GUIE_MODEL_PATH",
    "GUIE_YOUNG_MODULUS",
    "GUIE_POISSON_TATID",
    "GUIE_DENSITY",
    "GUIE_NUMBER_OF_ROOTS",
)


def main() -> int:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr, flush=True)
        return 2
    model = Path(os.environ["GUIE_MODEL_PATH"])
    if not model.is_file():
        print("Model file does not exist", file=sys.stderr, flush=True)
        return 2
    try:
        young = float(os.environ["GUIE_YOUNG_MODULUS"])
        poisson = float(os.environ["GUIE_POISSON_TATID"])
        density = int(os.environ["GUIE_DENSITY"])
        roots = int(os.environ["GUIE_NUMBER_OF_ROOTS"])
    except ValueError:
        print("Invalid numeric environment variable", file=sys.stderr, flush=True)
        return 2
    project_dir = Path(os.environ["GUIE_PROJECT_DIR"])
    if not project_dir.is_dir():
        print("Project directory does not exist", file=sys.stderr, flush=True)
        return 2
    solver_log = Path(os.environ["GUIE_JUSMAR_LOG"])
    cloud_info = Path(os.environ["GUIE_CLOUD_INFO_DIR"])
    logger.remove()
    logger.add(solver_log, encoding="utf-8")
    logger.info("guie2 test started: model={}, roots={}", model.name, roots)
    print("guie2 test started", flush=True)
    print("guie2 test stderr capture ready", file=sys.stderr, flush=True)
    time.sleep(float(os.environ.get("GUIE_TEST_SLEEP_SECONDS", "60")))
    cloud_info.write_text(
        json.dumps(
            {
                "test_only": True,
                "model_name": model.name,
                "young_modulus": young,
                "poisson_ratio": poisson,
                "density": density,
                "number_of_roots": roots,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    logger.info("guie2 test completed")
    print("guie2 test completed", flush=True)
    return int(os.environ.get("GUIE_TEST_EXIT_CODE", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
