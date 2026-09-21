"""The modal flow's fixed command and parameter-to-environment mapping."""

import os
import sys
from pathlib import Path

from core.config import Settings


def command() -> list[str]:
    # Replace here with the confirmed guierunner command when integrating the real script.
    return [sys.executable, str(Path(__file__).parent / "scripts" / "modal_test.py")]


def environment(
    parameters: dict, run_dir: Path, log_dir: Path, settings: Settings
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        GUIE_PROJECT_DIR=str(run_dir / "project"),
        GUIE_JUSMAR_LOG=str(log_dir / "jusmar.log"),
        GUIE_CLOUD_INFO_DIR=str(run_dir / "cloud_info.json"),
        GUIE_MODEL_PATH=parameters["model_path"],
        GUIE_YOUNG_MODULUS=str(parameters["young_modulus"]),
        GUIE_POISSON_TATID=str(parameters["poisson_ratio"]),
        GUIE_DENSITY=str(parameters["density"]),
        GUIE_NUMBER_OF_ROOTS=str(parameters["number_of_roots"]),
        GUIE_TEST_SLEEP_SECONDS=str(settings.test_sleep_seconds),
        GUIE_TEST_EXIT_CODE=str(settings.test_exit_code),
    )
    return env
