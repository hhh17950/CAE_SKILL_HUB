# CAE SkillHub · 719 流程执行服务

将固定 CAE 自动化脚本暴露成异步任务接口：提交参数 → 返回 `run_id` → Worker 执行 → 查询状态、日志与结果。当前使用读取环境变量、默认等待 60 秒的测试脚本，不启动真实 guie2，不计算物理结果。提供 HTTP/OpenAPI，尚未提供 MCP Server。

运行目标：Linux、Python 3.10。开发验证使用 Python 3.10.11。无需 Redis、Celery、uv 或 Agent 编排框架。

## Docker Compose 部署

两个常驻容器：API 接收请求，Worker 执行脚本；共享本地数据目录，不需要 Redis。数据库访问统一使用 SQLAlchemy + 标准库 sqlite3，完整事务通过线程池执行，不使用 aiosqlite。

先按 [开发与运维指南](docs/开发与运维指南.md) 配置 `.env`、模型目录和 UID 10001 的写权限，再执行：

```bash
docker compose build
docker compose run --rm --no-deps api python -m alembic upgrade head
docker compose up -d api worker
docker compose ps
docker compose logs -f api worker
```

迁移是人工部署步骤，不在容器启动时自动执行。API 默认只绑定宿主机 `127.0.0.1:8000`，外部接入通过反向代理。调用方的模型路径使用容器内 `/models/part.stp`，不是宿主机路径。

Dockerfile 默认 Python 3.10.11，运行非 root 用户；只封装测试脚本。真实 guie2 的图形会话、二进制依赖和许可证尚需另行接入。

Swagger `/docs` 使用仓库内 `static/docs` 的 JS、CSS 和图标，随镜像打包，内网运行不访问 CDN；在线 validator 已关闭，默认 ReDoc 页面停用。

## 本地开发启动

以下命令均在项目根目录执行。模型文件须已位于服务端可读目录。

```bash
python3.10 -m venv .venv
source .venv/bin/activate
PIP_CONFIG_FILE=/dev/null python -m pip --isolated install --index-url https://pypi.org/simple -r requirements.txt
cp .env.example .env
```

编辑 `.env`：填写 `CAE_MODEL_ROOT`、替换测试令牌。API 和 Worker 使用同一份配置。手动初始化数据库并启动 API：

```bash
python -m alembic upgrade head
python main.py
```

`python main.py` 默认监听 `127.0.0.1:8000`，只启动 API，不自动执行迁移或启动 Worker。原来的 `python -m uvicorn main:create_app --factory --host 127.0.0.1 --port 8000` 仍然可用，容器启动命令不变。

另一个终端，激活同一虚拟环境并进入项目根目录：

```bash
python worker.py
```

打开 `http://127.0.0.1:8000/docs`，通过 Authorize 输入令牌，提交 `POST /api/v1/guie-runs`。每次提交创建新任务，不做幂等去重；不要盲目自动重试提交。

## 代码结构

```text
main.py                    FastAPI 入口
worker.py                  单 Worker、队列轮询和状态回写
api/
  routes/                  真实 HTTP 接口：runs.py、health.py
  router.py                路由汇总
  schemas.py               Pydantic 请求/响应；其余文件负责鉴权等配套能力
services/
  modal.py                 当前流程固定命令、环境变量映射
  executor.py              异步子进程、超时和清理
  scripts/modal_test.py     测试流程，默认 sleep 60 秒
storage/
  models.py                SQLAlchemy 表结构，供 Alembic 生成迁移
  database.py              SQLAlchemy Engine、Session、线程池桥接
  repository.py            SQLAlchemy 查询及完整事务
core/                      配置、Loguru 日志
migrations/                Alembic 标准脚手架及版本文件
tests/                     自动化测试
scripts/                   文档与 OpenAPI 维护工具
docs/                      对接、架构、开发、测试指南
Dockerfile                 API/Worker 共用镜像
docker-compose.yaml        简单双容器配置、本地共享目录、手动迁移
.dockerignore              排除密钥、数据、虚拟环境等
static/docs/               固定版本 Swagger UI 离线资源和许可证
```

## 开发验证

```bash
PIP_CONFIG_FILE=/dev/null python -m pip --isolated install --index-url https://pypi.org/simple -r requirements-dev.txt
python -m pytest -q
CAE_RUN_SLOW_TESTS=1 python -m pytest -q tests/test_runs.py -k sixty
python -m ruff check .
python -m scripts.export_openapi --check
python -m scripts.check_docs
```

`requirements.txt` 只列直接运行依赖；测试依赖在 `requirements-dev.txt`。pip 仍会安装必要的传递依赖；这两份文件不是完整传递依赖锁。更多内容见 [文档索引](docs/README.md)。
