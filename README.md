# CAE SkillHub · 719 流程执行服务

将固定 CAE 自动化脚本暴露成异步任务接口：上传几何模型 → 返回 `run_id` → Worker 执行 → 查询状态、日志与结果。调用方用 multipart 上传模型文件，任务目录由服务在 `CAE_WORKSPACE_ROOT` 下按 `run_id` 创建，不接受调用方指定的服务端路径。提供 HTTP/OpenAPI 与 MCP（`/mcp`）两种接入方式。执行默认使用外部受控启动器 `CAE_GUIERUNNER_PATH`，它负责运行仓库内的流程脚本 `services/scripts/modal_nogui_process.py`；未配置启动器时回退到仓库自带的示例脚本 `services/scripts/modal_test.py`（只回显参数、写占位结果，不是真实 CAE 仿真）。真实流程尚不可用时，可用 `python -m services.scripts.simulate_cloud_run` 造一个已完成任务，用来演示智能体取云图的链路（模拟数据，不是仿真结果）。

运行目标：Linux、Python 3.10。开发验证使用 Python 3.10.11。无需 Redis、Celery、uv 或 Agent 编排框架。

## Docker Compose 部署

两个常驻容器：API 接收请求，Worker 执行脚本；共享本地数据目录，不需要 Redis。数据库访问统一使用 SQLAlchemy + 标准库 sqlite3，完整事务通过线程池执行，不使用 aiosqlite。

先按 [开发与运维指南](docs/开发与运维指南.md) 配置 `.env`、任务工作目录和 CAE 安装目录，再执行：

```bash
docker compose build
docker compose run --rm --no-deps cae-run python -m alembic upgrade head
docker compose up -d cae-run worker
docker compose ps
docker compose logs -f cae-run worker
```

迁移是人工部署步骤，不在容器启动时自动执行：Compose 只启动 API 和 Worker，不执行 `alembic upgrade head`。容器把 `8000` 发布到宿主机 `0.0.0.0:8000`，需要限制来源时用反向代理或防火墙。几何模型由调用方上传，不需要把宿主机模型目录挂进容器；`.env` 中的 `WORKSPACE` 与 `SSTA_CAE_PATH` 分别挂载为任务工作目录和 CAE 安装目录，两个容器都必须能访问。

Dockerfile 以内部 `centos-conda:cos7-24.9.2-x86_64` 为基础镜像，把仓库复制到 `/opt/cae_service` 并安装 `requirements.txt`。当前 Dockerfile 未切换到非 root 用户，容器内以 root 运行；投产前应按合规要求补齐非 root 运行与镜像扫描。真实 guie2 的图形会话、二进制依赖和许可证尚需另行接入。

Swagger `/docs` 使用仓库内 `static/docs` 的 JS、CSS 和图标，随镜像打包，内网运行不访问 CDN；在线 validator 已关闭，默认 ReDoc 页面停用。

## 本地开发启动

以下命令均在项目根目录执行。任务工作目录由 `.env` 的 `CAE_WORKSPACE_ROOT` 指定，必须已存在且对运行账户可写。

```bash
python3.10 -m venv .venv
source .venv/bin/activate
PIP_CONFIG_FILE=/dev/null python -m pip --isolated install --index-url https://pypi.org/simple -r requirements.txt
cp .env.example .env
```

编辑 `.env`：至少确认 `CAE_WORKSPACE_ROOT`、`CAE_DATABASE_PATH`、`CAE_SERVICE_LOG_ROOT` 指向本机可写目录；要执行真实流程还需设置 `CAE_GUIERUNNER_PATH`（不设置时用仓库自带示例脚本跑通链路）。云图由 VTK 渲染，需要 Mesa 的 `libGL`：`CAE_MESA_LIB_PATH` 留空时按 `$SSTA_CAE_PATH/mesa/lib`、`/opt/mesa/lib` 依次探测，`./start.sh` 会自动把它加进 `LD_LIBRARY_PATH`（手动启动进程时要自己 export）。API 和 Worker 使用同一份配置。手动初始化数据库并启动 API：

```bash
python -m alembic upgrade head
python main.py
```

`python main.py` 默认监听 `127.0.0.1:8000`（只接受本机连接），只启动 API，不自动执行迁移或启动 Worker。要让其他机器访问，用 `CAE_API_HOST=0.0.0.0 python main.py`，或直接用下面的 `./start.sh start`（默认绑定 `0.0.0.0`）。原来的 `python -m uvicorn main:create_app --factory --host 127.0.0.1 --port 8000` 仍然可用，容器启动命令不变。

注意区分**监听地址**和**对外公布地址**：前者决定谁能连上（`0.0.0.0` = 接受其他机器，`127.0.0.1` = 只有本机）；后者是 `.env` 里的 `CAE_PUBLIC_BASE_URL`，只用于拼云图 `image_url`，必须填调用方能访问到的地址（内网即 `http://<服务器IP>:8000`），填 `0.0.0.0` 或 `127.0.0.1` 时智能体拿不到云图。详见 [开发与运维指南](docs/开发与运维指南.md)。

另一个终端，激活同一虚拟环境并进入项目根目录：

```bash
python worker.py
```

打开 `http://127.0.0.1:8000/docs`，在 `POST /api/v1/guie-runs/modal` 上传几何模型并填写物理参数即可提交；服务自身不做鉴权，需要访问控制请在部署层实现。任务目录、日志和结果都由服务在 `CAE_WORKSPACE_ROOT` 下按 `run_id` 生成，调用方不能指定路径。每次提交创建新任务，不做幂等去重；不要盲目自动重试提交。智能体接入可直接使用 `/mcp`：服务在 `initialize` 的 `instructions` 里给出调用顺序与"必须先向用户要几何模型文件"等约定，工具另有标题与中文说明；字段与轮询说明见 [对接指南](docs/对接指南.md)。

### 一键启停（start.sh）

上面两个终端可以合并为仓库根目录的 `start.sh`，API 与 Worker 都在后台运行：

```bash
./start.sh start              # 先 alembic upgrade head，再启动 API + Worker
./start.sh status             # PID、运行时长、/healthz
./start.sh logs worker -f     # 跟踪后台日志
./start.sh stop               # 先停 API，再停 Worker
./start.sh restart --no-migrate
```

PID 和后台 stdout/stderr 写入 `.runtime/`，已被 `.gitignore` 忽略。`start` 只在当前没有服务运行时才自动迁移，每次启动都迁移可用 `--no-migrate` 关闭；`migrate` 子命令在服务运行时会拒绝执行。`--host`、`--port` 可覆盖监听地址，`-f` 改为前台运行并用 Ctrl+C 同时停止两个进程，完整用法见 `./start.sh help`。脚本只在 Linux/WSL 下可用，Worker 依赖 `fcntl`；容器部署不使用该脚本。

## 代码结构

```text
main.py                    FastAPI 入口、lifespan 与 /mcp 挂载
worker.py                  单 Worker、队列轮询和状态回写
api/
  routes/                  真实 HTTP 接口：runs.py、health.py、docs.py
  router.py                路由汇总
  schemas.py               Pydantic 响应模型与 RFC 9457 错误结构
  errors.py / openapi.py / body_limit.py   错误、契约与请求体限制配套能力
services/
  modal.py                 当前流程固定命令、环境变量映射
  executor.py              异步子进程、超时和清理
  paths.py                 任务目录与日志、结果路径
  mcp_server.py            MCP 工具与 /mcp 传输层安全配置
  cloud_png.py             由 cloud_info.json 生成云图
  scripts/modal_nogui_process.py   交给外部启动器执行的流程脚本
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
start.sh                   API + Worker 一键启停脚本（仅 Linux/WSL）
docker-compose.yaml        简单双容器配置、本地共享目录、手动迁移
.dockerignore              排除密钥、数据、虚拟环境等
.gitattributes             保护 vendored 资源与 shell 脚本的换行
static/docs/               固定版本 Swagger UI 离线资源和许可证
```

## 开发验证

```bash
PIP_CONFIG_FILE=/dev/null python -m pip --isolated install --index-url https://pypi.org/simple -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
python -m scripts.export_openapi --check
python -m scripts.check_docs
```

`python -m scripts.export_openapi` 会从当前代码重新生成 `docs/contracts/openapi.json`，`--check` 用来确认它没有过期；`python -m scripts.check_docs` 校验文档链接、示例 JSON 和对接指南的路由表。

`requirements.txt` 只列直接运行依赖；测试依赖在 `requirements-dev.txt`。pip 仍会安装必要的传递依赖；这两份文件不是完整传递依赖锁。更多内容见 [文档索引](docs/README.md)。
