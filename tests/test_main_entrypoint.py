import runpy
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI


def test_main_starts_api_without_starting_worker_or_migrating():
    entrypoint = Path(__file__).resolve().parents[1] / "main.py"
    with patch("uvicorn.run") as run:
        runpy.run_path(str(entrypoint), run_name="__main__")
    run.assert_called_once()
    assert isinstance(run.call_args.args[0], FastAPI)
    assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 8000}
