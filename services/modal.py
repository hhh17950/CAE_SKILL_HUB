"""The modal flow's fixed command and parameter-to-environment mapping."""

import os
import sys
from pathlib import Path

from core.config import Settings

SCRIPTS = Path(__file__).resolve().parent / "scripts"


def command(settings: Settings) -> list[str]:
    """The command for the modal flow.

    ``CAE_GUIERUNNER_PATH`` is the real controlled launcher: it runs the flow script in nogui
    mode. Without it the repository's example script runs instead, so the pipeline stays
    exercisable locally; that script is not a CAE simulation.
    """
    if settings.guierunner_path is None:
        return [sys.executable, str(SCRIPTS / "modal_test.py")]
    return [str(settings.guierunner_path), "nogui", str(SCRIPTS / "modal_nogui_process.py")]


def environment(parameters: dict) -> dict[str, str]:
    run_dir = Path(parameters["run_dir"])
    env = os.environ.copy()
    env.update(
        GUIE_PROJECT_DIR=str(run_dir / "project"),
        GUIE_JUSMAR_LOG=str(run_dir / "jusmar.log"),
        GUIE_CLOUD_INFO_DIR=str(run_dir / "cloud_info.json"),
        GUIE_MODEL_PATH=str(run_dir / "model" / parameters["model_filename"]),
        GUIE_YOUNG_MODULUS=str(parameters["young_modulus"]),
        GUIE_POISSON_TATIO=str(parameters["poisson_ratio"]),
        GUIE_DENSITY=str(parameters["density"]),
        GUIE_NUMBER_OF_ROOTS=str(parameters["number_of_roots"]),
        GUIE_TEST_SLEEP_SECONDS=str(parameters["test_sleep_seconds"]),
        GUIE_TEST_EXIT_CODE=str(parameters["test_exit_code"]),
    )
    return env
