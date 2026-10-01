"""Contract checks for the one-command API + Worker launcher (start.sh)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "start.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")


def run(*args, runtime_dir):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        env={**os.environ, "CAE_RUNTIME_DIR": str(runtime_dir)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_launcher_is_syntactically_valid():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    assert result.returncode == 0, result.stderr


def test_launcher_has_no_crlf_line_endings():
    assert b"\r\n" not in SCRIPT.read_bytes()


def test_help_documents_every_subcommand(tmp_path):
    result = run("help", runtime_dir=tmp_path)
    assert result.returncode == 0
    for command in ("start", "stop", "restart", "status", "logs", "migrate"):
        assert command in result.stdout
    for option in ("--host", "--port", "--no-migrate", "--foreground"):
        assert option in result.stdout


def test_unknown_command_fails(tmp_path):
    result = run("definitely-not-a-command", runtime_dir=tmp_path)
    assert result.returncode != 0
    assert "未知命令" in result.stderr


def test_invalid_port_fails_without_starting_anything(tmp_path):
    result = run("start", "--port", "http", runtime_dir=tmp_path)
    assert result.returncode != 0
    assert "--port" in result.stderr


def test_status_and_stop_are_safe_without_running_services(tmp_path):
    status = run("status", runtime_dir=tmp_path)
    assert status.returncode == 1
    assert "未运行" in status.stdout

    stopped = run("stop", runtime_dir=tmp_path)
    assert stopped.returncode == 0
    assert "未在运行" in stopped.stdout


def test_logs_reports_missing_files(tmp_path):
    result = run("logs", runtime_dir=tmp_path)
    assert result.returncode == 1
    assert "不存在" in result.stderr
