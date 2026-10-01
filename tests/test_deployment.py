"""Deployment contract: the Compose file must match the code it starts.

Two layers:

* a dependency-free text check that always runs and pins the regressions that actually
  happened (``CAE_WORKSPACE_PATH`` instead of the real ``CAE_WORKSPACE_ROOT`` field, the
  old ``/app/workspace`` mount, the ``api``/``worker`` service names);
* ``docker compose config`` when the CLI is present, which validates interpolation and the
  resolved environment/volumes of both services.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.yaml"
CONTAINER_WORKSPACE = "/opt/cae_service/workspace"
CONTAINER_CAE_INSTALL = "/opt/ssta-cae"


def compose_text() -> str:
    return COMPOSE_FILE.read_text(encoding="utf-8")


def test_service_names_and_commands():
    text = compose_text()
    assert re.search(r"^  cae-run:$", text, re.MULTILINE)
    assert re.search(r"^  worker:$", text, re.MULTILINE)
    # The API service was renamed from "api"; the old name must not come back.
    assert not re.search(r"^  api:$", text, re.MULTILINE)
    assert "main:create_app" in text
    assert '["python", "worker.py"]' in text


def test_container_paths_and_environment_names():
    text = compose_text()
    # Settings field is workspace_root (CAE_WORKSPACE_ROOT); CAE_WORKSPACE_PATH never existed.
    assert "CAE_WORKSPACE_PATH" not in text
    assert f"CAE_WORKSPACE_ROOT: {CONTAINER_WORKSPACE}" in text
    assert f"CAE_DATABASE_PATH: {CONTAINER_WORKSPACE}/guie.db" in text
    assert f"CAE_SERVICE_LOG_ROOT: {CONTAINER_WORKSPACE}/logs" in text
    assert CONTAINER_CAE_INSTALL in text
    assert "/app/" not in text
    # The launcher is optional: it is passed through, blank when unset.
    assert "CAE_GUIERUNNER_PATH: ${CAE_GUIERUNNER_PATH:-}" in text


def test_public_base_url_is_never_a_listen_or_loopback_address():
    """The cloud-image URLs handed to callers are built from CAE_PUBLIC_BASE_URL.

    ``0.0.0.0`` is a listen-only address and ``127.0.0.1`` is loopback, so either one produces an
    ``image_url`` that resolves on the server and nowhere else - the run still reports success, so
    nothing else surfaces the mistake. This is a plain text check on purpose: it must also run on
    hosts without the Docker CLI, where ``docker compose config`` is skipped.
    """
    text = compose_text()
    match = re.search(r"CAE_PUBLIC_BASE_URL:\s*(\S+)", text)
    assert match, "docker-compose.yaml must set CAE_PUBLIC_BASE_URL for the API service"
    value = match.group(1)
    for unreachable in ("0.0.0.0", "127.0.0.1", "localhost"):
        assert unreachable not in value, f"compose must not advertise {unreachable}: {value}"


def test_env_example_advertises_a_reachable_address():
    """``cp .env.example .env`` is the documented setup step, so the template must be usable.

    The wildcard value that used to live here survived until a real deployment, where it silently
    turned every cloud-image URL into an unreachable one.
    """
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    match = re.search(r"^CAE_PUBLIC_BASE_URL=(\S+)$", text, re.MULTILINE)
    assert match, ".env.example must set CAE_PUBLIC_BASE_URL"
    value = match.group(1)
    assert value.startswith("http://"), value
    for unreachable in ("0.0.0.0", "127.0.0.1", "localhost"):
        assert unreachable not in value, f".env.example must not advertise {unreachable}: {value}"


def test_only_the_api_publishes_a_port():
    text = compose_text()
    published = re.findall(r'^\s+- "([\d.]+:\d+:\d+)"$', text, re.MULTILINE)
    assert published == ["0.0.0.0:8000:8000"]


def test_migrations_stay_a_manual_step():
    """Neither container may auto-migrate; the deployment runs alembic upgrade head itself."""
    text = compose_text()
    assert "alembic upgrade head" not in text
    assert "alembic" not in text


def compose_config():
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable; does not require a running daemon")
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        pytest.skip("Docker Compose plugin unavailable")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--env-file",
            ".env.example",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env={**os.environ, "CAE_ENV_FILE": ".env.example"},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def build_context(service) -> str:
    build = service.get("build")
    context = build.get("context") if isinstance(build, dict) else build
    return Path(context or ".").name


def test_compose_contract():
    config = compose_config()
    api, worker = config["services"]["cae-run"], config["services"]["worker"]
    assert set(config["services"]) == {"cae-run", "worker"}

    # Same Dockerfile, different image tags: the API ships the docs assets, the Worker does not.
    assert build_context(api) == build_context(worker) == ROOT.name
    assert "main:create_app" in " ".join(api["command"])
    assert worker["command"] == ["python", "worker.py"]
    assert "ports" not in worker and "depends_on" not in worker

    for service in (api, worker):
        assert service["environment"]["CAE_DATABASE_PATH"] == f"{CONTAINER_WORKSPACE}/guie.db"
        assert service["environment"]["CAE_WORKSPACE_ROOT"] == CONTAINER_WORKSPACE
        volumes = {item["target"]: item for item in service["volumes"]}
        assert set(volumes) == {CONTAINER_WORKSPACE, CONTAINER_CAE_INSTALL}
    assert api["volumes"] == worker["volumes"]

    assert api["environment"]["CAE_PUBLIC_BASE_URL"].startswith("http://")
    assert "CAE_GUIERUNNER_PATH" in worker["environment"]
