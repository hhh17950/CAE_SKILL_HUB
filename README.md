# CAE Skill 工作流中台

版本：0.2.0，客户契约草案。技术栈为 Python 3.12+、FastAPI、Pydantic v2、asyncio、aiosqlite。

当前提供类型化模态分析协议、持久化作业和独立 Mock Worker。客户端提交一份分析请求，Worker 在隔离 Python 进程内执行九个模拟 SDK 操作，再跟踪模拟求解状态并收集结果，共 11 个步骤。

**当前没有真实 `jusmar_app` SDK，也没有执行 `guierunner`。输出仅为 Mock 执行摘要，没有 CAD 解析、网格、频率、位移或反力数值。** `CAE_PROVIDER=jusmar` 会拒绝启动，不会回退为 Mock。

## 快速运行

从项目根目录执行，API 与 Worker 使用同一份配置、数据库及文件目录。

```bash
uv sync --cache-dir .uv-cache
```

终端一启动 API：

```bash
.venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

终端二启动 Worker：

```bash
.venv/bin/python -m app.execution.worker
```

终端三运行模拟合作方客户端：

```bash
.venv/bin/python -m examples.modal_workflow
```

`passed: true` 表示协议、幂等、执行步骤和结果文件校验通过。测试几何文件是文本夹具，不能用于真实 CAE 分析。客户端没有 LLM，只通过 HTTP 完成资产上传、预检、提交、轮询和结果下载。

Swagger 地址为 `http://127.0.0.1:8000/docs`。默认本地测试令牌 `local-test-token`，对应身份 `demo`。可参考 [.env.example](./.env.example) 创建配置。默认凭据和本地部署不适合直接开放到公网。

## 当前协议

```text
上传几何资产 → 可选预检 → 提交分析 → 查询运行 → 取得摘要及文件
```

`POST /api/v1/analysis-runs` 返回 `202`、`run_id` 和查询地址，要求 `Idempotency-Key`。只有独立 Worker 才会执行队列中的任务。仅启动 API 时，任务保持 `queued` 是预期行为。

当前只支持 `modal_analysis@1.0`：自由边界、单一线弹性各向同性材料、全部实体统一赋材的协议模拟。材料物性和几何单位必须明确提供。详细字段见 [对接指南](docs/对接指南.md)。

## 验证与导出

```bash
.venv/bin/ruff check app tests examples scripts
.venv/bin/pytest -q
.venv/bin/python -m scripts.export_openapi --check
.venv/bin/python -m scripts.check_docs
```

需要自动启动临时 API 和 Worker 并验证 API 进程重启时，可运行 `.venv/bin/python -m scripts.acceptance_workflow`。

修改公开模型后重新生成合同与文档交付包：

```bash
.venv/bin/python -m scripts.export_openapi
.venv/bin/python -m scripts.package_docs
```

## 文档与维护入口

- [文档总览及实现状态](./docs/README.md)
- [系统架构与技术方案](docs/系统架构与技术方案.md)
- [对接指南](docs/对接指南.md)与[OpenAPI](./docs/contracts/openapi.json)
- [开发与运维指南](docs/开发与运维指南.md)
- [测试与交付指南](docs/测试与交付指南.md)

默认关闭旧九接口的直接调用入口。它们的代码和测试作为底层能力回归保留，可通过 `CAE_ENABLE_LEGACY_API=true` 显式启用；该入口使用独立内存状态，不能与新的持久化工作流交换工程 ID。当前客户文档只描述新工作流协议。
