"""The modal flow's fixed command and parameter-to-environment mapping."""

import os
import sys
from pathlib import Path

from core.config import Settings


def command(settings: Settings) -> list[str]:
    # Replace here with the confirmed guierunner command when integrating the real script.
    modal_nogui_process_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "modal_nogui_process.py")
    if settings.guierunner_path is not None:
        return [str(settings.guierunner_path), "nogui", modal_nogui_process_path]
    return [sys.executable, str(Path(__file__).parent / "scripts" / "modal_test.py")]


def environment(parameters: dict) -> dict[str, str]:
    run_dir = Path(parameters["run_dir"])
    env = os.environ.copy()
    env.update(
        GUIE_PROJECT_DIR=str(run_dir / "project"),
        GUIE_JUSMAR_LOG=str(run_dir / "jusmar.log"),
        GUIE_CLOUD_INFO_DIR=str(run_dir / "cloud_info.json"),
        GUIE_MODEL_PATH=str(run_dir / "model" / parameters["model_filename"]),
        GUIE_YOUNG_MODULUS=str(parameters["young_modulus"]),
        GUIE_POISSON_TATID=str(parameters["poisson_ratio"]),
        GUIE_DENSITY=str(parameters["density"]),
        GUIE_NUMBER_OF_ROOTS=str(parameters["number_of_roots"]),
        GUIE_TEST_SLEEP_SECONDS=str(parameters["test_sleep_seconds"]),
        GUIE_TEST_EXIT_CODE=str(parameters["test_exit_code"]),
    )
    return env
