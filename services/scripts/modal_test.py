"""Example flow script for local integration; it is NOT a CAE simulation.

`services/modal.py` runs this script only when `CAE_GUIERUNNER_PATH` is unset or blank, so the
API and Worker can be exercised without the real guierunner. It echoes the parameters it
received, writes the task outputs and honours `GUIE_TEST_SLEEP_SECONDS` / `GUIE_TEST_EXIT_CODE`.
Replace it (or set `CAE_GUIERUNNER_PATH`) before doing any real work; the numbers below are not
physical results.
"""

import json
import os
import sys
import time
from pathlib import Path

# (环境变量名, 默认值, 转换函数)；服务端始终会设置前 8 项，测试用的两项可省略。
SPEC = (
    ("GUIE_PROJECT_DIR", None, str),
    ("GUIE_JUSMAR_LOG", None, str),
    ("GUIE_CLOUD_INFO_DIR", None, str),
    ("GUIE_MODEL_PATH", None, str),
    ("GUIE_YOUNG_MODULUS", 2.0e11, float),
    ("GUIE_POISSON_TATIO", 0.3, float),
    ("GUIE_DENSITY", 7850, int),
    ("GUIE_NUMBER_OF_ROOTS", 10, int),
    ("GUIE_TEST_SLEEP_SECONDS", 0.0, float),
    ("GUIE_TEST_EXIT_CODE", 0, int),
)


def parameters() -> dict:
    values = {}
    for name, default, cast in SPEC:
        raw = os.getenv(name)
        if raw is None or raw == "":
            if default is None:
                raise SystemExit(f"缺少环境变量 {name}")
            values[name] = default
        else:
            values[name] = cast(raw)
    return values


def main() -> int:
    values = parameters()
    model = Path(values["GUIE_MODEL_PATH"])
    if not model.is_file():
        print(f"模型文件不存在: {model}", file=sys.stderr, flush=True)
        return 2

    print(f"test completed model={model.name} bytes={model.stat().st_size}", flush=True)
    print("capture ready", file=sys.stderr, flush=True)
    Path(values["GUIE_JUSMAR_LOG"]).write_text("test completed\n", encoding="utf-8")

    time.sleep(values["GUIE_TEST_SLEEP_SECONDS"])

    exit_code = values["GUIE_TEST_EXIT_CODE"]
    if exit_code == 0:
        # 真实流程会为每个模态写一条 {vtk_file, cloud_file_name, frequency} 记录。这里没有
        # VTK 结果，只回显参数并标记自己，Worker 因此会跳过云图渲染。
        echo = {
            "test_only": True,
            "model_name": model.name,
            "parameters": {
                "young_modulus": values["GUIE_YOUNG_MODULUS"],
                "poisson_ratio": values["GUIE_POISSON_TATIO"],
                "density": values["GUIE_DENSITY"],
                "number_of_roots": values["GUIE_NUMBER_OF_ROOTS"],
            },
        }
        Path(values["GUIE_CLOUD_INFO_DIR"]).write_text(
            json.dumps(echo, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
