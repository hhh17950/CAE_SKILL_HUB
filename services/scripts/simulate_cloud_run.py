"""Fabricate a finished run so the cloud-image path can be exercised without a real CAE flow.

Development aid, not a CAE simulation: no Worker runs, nothing is solved, and every frequency and
picture is a placeholder. The task directory is filled in exactly the shape the real flow produces
- ``services/scripts/modal_nogui_process.py`` writes ``cloud_info.json`` with ``vtk_file`` /
``cloud_file_name`` / ``frequency`` per mode, and the Worker then renders the PNG named by
``cloud_file_name`` - so the agent-facing path (``get_run_result`` -> ``image_url`` -> HTTP GET)
can be demonstrated on a host that has neither the guierunner nor VTK.

Run it on the host serving the API, against an already migrated database:

    python -m services.scripts.simulate_cloud_run --modes 3

The record is inserted in one transaction with status ``succeeded`` instead of going through
submit -> claim -> finish: a live Worker could claim the run in between and overwrite this
directory with a real execution attempt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import zlib
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import insert

from core.config import Settings
from services import paths
from storage.database import run_in_thread
from storage.models import guie_runs
from storage.repository import RunRepository, utc_now

IMAGE_SIZE = (640, 400)
NOTE = "模拟数据（非真实 CAE 仿真）：本任务由 services/scripts/simulate_cloud_run.py 生成。\n"


def _chunk(tag: bytes, payload: bytes) -> bytes:
    """One PNG chunk, including the CRC the format requires."""
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def write_png(path: Path, mode: int, modes: int, size: tuple[int, int] = IMAGE_SIZE) -> None:
    """Write a real, dependency-free PNG; the real flow renders these with VTK instead."""
    width, height = size
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter type of this row
        for x in range(width):
            stripe = x * modes // width
            rows += bytes(
                (
                    255 if stripe == mode - 1 else 60,
                    40 + y * 150 // height,
                    max(40, 200 - stripe * 40),
                )
            )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + _chunk(b"IEND", b"")
    )


def build_cloud_info(run_dir: Path, modes: int, frequencies: list[float] | None) -> dict:
    """The mapping ``modal_nogui_process.py`` writes, with simulated values."""
    project = paths.project_dir(run_dir)
    cloud_dir = project / "cloud_png"
    cloud_dir.mkdir(parents=True, exist_ok=True)
    vtk_dir = project / "results"
    vtk_dir.mkdir(parents=True, exist_ok=True)

    cloud_info = {}
    for index in range(1, modes + 1):
        image = cloud_dir / f"cloud_3d_{index}.png"
        write_png(image, index, modes)
        cloud_info[str(index)] = {
            # The real entry points at a solved VTK result; nothing reads it here.
            "vtk_file": str(vtk_dir / f"mode_{index}.vtk"),
            "cloud_file_name": str(image),
            "frequency": (
                frequencies[index - 1] if frequencies else round(120.0 * index**1.5, 4)
            ),
        }
    return cloud_info


def _insert_finished(database, run_id: str, parameters: dict) -> None:
    """One transaction: the run only ever exists as finished, so no Worker can claim it."""
    stamp = utc_now()
    with database.sessions.begin() as session:
        session.execute(
            insert(guie_runs).values(
                run_id=run_id,
                owner="public",
                status="succeeded",
                request_json=json.dumps(parameters, ensure_ascii=False),
                created_at=stamp,
                started_at=stamp,
                finished_at=stamp,
                exit_code=0,
                error=None,
            )
        )


async def simulate(
    settings: Settings,
    run_id: str,
    modes: int = 3,
    model_filename: str = "demo.stp",
    frequencies: list[float] | None = None,
) -> dict:
    """Create a finished run (task directory + database row) and return the stored record.

    ``run_id`` must be unused; ``paths.create_task_dir`` raises ``FileExistsError`` otherwise, and
    ``RunRepository.initialize()`` raises ``RuntimeError`` when the database was never migrated.
    """
    if modes < 1:
        raise ValueError("modes 必须 >= 1")
    if frequencies is not None and len(frequencies) != modes:
        raise ValueError("频率个数必须与 modes 一致")

    parameters = {
        "young_modulus": 2.0e11,
        "poisson_ratio": 0.3,
        "density": 7850,
        "number_of_roots": modes,
        "model_filename": model_filename,
        "simulated": True,
    }
    # An unmigrated database is refused before anything is written to disk.
    repository = RunRepository(settings.database_path)
    await repository.initialize()
    try:
        run_dir = paths.create_task_dir(settings, run_id)
        paths.model_path(run_dir, model_filename).write_bytes(b"ISO-10303-21;\n")  # placeholder
        cloud_info = build_cloud_info(run_dir, modes, frequencies)
        paths.cloud_info(run_dir).write_text(
            json.dumps(cloud_info, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        paths.stdout_log(run_dir).write_text(NOTE, encoding="utf-8")
        paths.stderr_log(run_dir).write_text("", encoding="utf-8")
        paths.jusmar_log(run_dir).write_text(NOTE, encoding="utf-8")

        await run_in_thread(_insert_finished, repository.database, run_id, parameters)
        return await repository.get(run_id)
    finally:
        await repository.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成一个“已完成”的模拟任务（云图与频率都是占位数据，不是真实 CAE 仿真）"
    )
    parser.add_argument("--run-id", default=None, help="任务 ID，默认 run_demo<随机 16 位>")
    parser.add_argument("--modes", type=int, default=3, help="模态与云图数量，默认 3")
    parser.add_argument("--model", default="demo.stp", help="写入任务目录的占位模型文件名")
    parser.add_argument(
        "--frequency", type=float, action="append", help="按顺序指定各模态频率，可重复使用"
    )
    args = parser.parse_args(argv)

    settings = Settings()
    run_id = args.run_id or f"run_demo{uuid4().hex[:16]}"
    try:
        row = asyncio.run(simulate(settings, run_id, args.modes, args.model, args.frequency))
    except FileExistsError:
        print(f"任务目录已存在，请换一个 --run-id：{paths.task_dir(settings, run_id)}")
        return 2
    except (RuntimeError, ValueError) as exc:
        print(f"无法生成模拟任务：{exc}")
        return 2

    base = settings.public_base_url.rstrip("/")
    run_dir = paths.task_dir(settings, run_id)
    print(f"已生成模拟任务 run_id={row['run_id']} status={row['status']} exit_code={row['exit_code']}")
    print(f"任务目录：{run_dir}")
    print(f"云图信息：{paths.cloud_info(run_dir)}")
    print("云图下载地址（get_run_result 返回的 image_url 即为这些）：")
    for index in range(1, args.modes + 1):
        print(f"  {base}/api/v1/guie-runs/{run_id}/cloud/cloud_3d_{index}.png")
    print("提醒：模拟数据不是仿真结果；CAE_PUBLIC_BASE_URL 必须是调用方能访问到的地址。")
    if urlsplit(base).hostname in {"0.0.0.0", "::", "::0"}:
        print(
            f"警告：CAE_PUBLIC_BASE_URL={base} 用了 0.0.0.0（监听地址，不是可访问地址），"
            "调用方拿到的 image_url 打不开；请改成服务器 IP，例如 http://192.168.16.128:8000。"
        )
    elif urlsplit(base).hostname in {"127.0.0.1", "localhost", "::1"}:
        print(
            f"警告：CAE_PUBLIC_BASE_URL={base} 是回环地址，只有本机能访问，"
            "其他机器拿到的 image_url 打不开；请改成服务器 IP，例如 http://192.168.16.128:8000。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
