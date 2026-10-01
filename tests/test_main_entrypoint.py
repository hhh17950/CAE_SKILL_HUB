import runpy
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI


def test_main_starts_api_without_starting_worker_or_migrating(monkeypatch):
    entrypoint = Path(__file__).resolve().parents[1] / "main.py"
    for name in ("CAE_API_HOST", "CAE_API_PORT"):
        monkeypatch.delenv(name, raising=False)
    with patch("uvicorn.run") as run:
        runpy.run_path(str(entrypoint), run_name="__main__")
    run.assert_called_once()
    assert isinstance(run.call_args.args[0], FastAPI)
    assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 8000}


def test_main_entrypoint_honours_the_shared_host_and_port_switches(monkeypatch):
    """``python main.py`` must be able to accept other machines, exactly like ``start.sh``.

    ``127.0.0.1`` is the loopback address: it is reachable only from the same host, so a deployment
    that runs this entrypoint would silently refuse every other machine, including an agent fetching
    a cloud image. The default stays loopback (safe for local runs) and CAE_API_HOST opens it up.
    """
    entrypoint = Path(__file__).resolve().parents[1] / "main.py"
    monkeypatch.setenv("CAE_API_HOST", "0.0.0.0")
    monkeypatch.setenv("CAE_API_PORT", "9001")
    with patch("uvicorn.run") as run:
        runpy.run_path(str(entrypoint), run_name="__main__")
    run.assert_called_once()
    assert run.call_args.kwargs == {"host": "0.0.0.0", "port": 9001}
