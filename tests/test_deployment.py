import json
import os
import shutil
import subprocess

import pytest


def test_compose_contract():
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable; does not require a running daemon")
    version = subprocess.run(["docker", "compose", "version"], capture_output=True)
    if version.returncode:
        pytest.skip("Docker Compose plugin unavailable")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--env-file",
            ".env.example",
            "config",
            "--format",
            "json",
        ],
        env={**os.environ, "CAE_ENV_FILE": ".env.example"},
        capture_output=True,
        text=True,
        check=True,
    )
    config = json.loads(result.stdout)
    api, worker = config["services"]["api"], config["services"]["worker"]
    assert set(config["services"]) == {"api", "worker"}
    assert api["image"] == worker["image"]
    assert "main:create_app" in api["command"]
    assert worker["command"] == ["python", "worker.py"]
    assert "ports" not in worker and "depends_on" not in worker
    for service in (api, worker):
        assert service["init"]
        assert service["environment"]["CAE_DATABASE_PATH"] == "/app/workspace/guie.db"
        volumes = {item["target"]: item for item in service["volumes"]}
        assert set(volumes) == {"/app/workspace", "/app/static/logs", "/models"}
        assert volumes["/models"]["read_only"]
    assert api["volumes"] == worker["volumes"]
