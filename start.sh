#!/usr/bin/env bash
# One-command launcher for the API (`main.py`) and the single Worker (`worker.py`).
#
#   ./start.sh start    [--host H] [--port P] [--no-migrate] [-f]   start API + Worker in background
#   ./start.sh stop                                                 stop API, then Worker
#   ./start.sh restart  [same options as start]                     stop then start
#   ./start.sh status                                               PIDs, uptime and /healthz
#   ./start.sh logs     [api|worker|both] [-n LINES] [-f]           tail the background logs
#   ./start.sh migrate                                              run alembic upgrade head only
#   ./start.sh help
#
# Environment knobs: CAE_PYTHON, CAE_RUNTIME_DIR, CAE_API_HOST, CAE_API_PORT,
#   CAE_START_TIMEOUT_SECONDS (30), CAE_STOP_TIMEOUT_SECONDS (45).
#
# Migrations stay a manual deployment step: `start` runs `alembic upgrade head` only when
# neither service is running yet, and `--no-migrate` skips it entirely.

set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd -- "$SCRIPT_DIR" || exit 1

RUNTIME_DIR="${CAE_RUNTIME_DIR:-$SCRIPT_DIR/.runtime}"
API_HOST="${CAE_API_HOST:-0.0.0.0}"
API_PORT="${CAE_API_PORT:-8000}"
START_TIMEOUT="${CAE_START_TIMEOUT_SECONDS:-30}"
STOP_TIMEOUT="${CAE_STOP_TIMEOUT_SECONDS:-45}"

API_PID_FILE="$RUNTIME_DIR/api.pid"
WORKER_PID_FILE="$RUNTIME_DIR/worker.pid"
API_ADDR_FILE="$RUNTIME_DIR/api.addr"
API_LOG="$RUNTIME_DIR/api.out"
WORKER_LOG="$RUNTIME_DIR/worker.out"

PY=""
DO_MIGRATE=1
FOREGROUND=0
FOLLOW=0
LOG_TARGET="both"
LOG_LINES=50

log() { printf '[start.sh] %s\n' "$*"; }
warn() { printf '[start.sh] 警告: %s\n' "$*" >&2; }
die() {
  printf '[start.sh] 错误: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'TEXT'
CAE SkillHub 一键启停脚本：同时后台运行 API 与 Worker。

用法:
  ./start.sh start [选项]               启动 API + Worker（后台运行，省略命令时默认执行）
  ./start.sh stop                       停止 API 与 Worker
  ./start.sh restart [选项]             先停止再启动
  ./start.sh status                     查看 PID、运行时长与 /healthz
  ./start.sh logs [api|worker|both] [-n 行数] [-f]
                                        查看后台日志（-f 持续跟踪）
  ./start.sh migrate                    只执行数据库迁移 alembic upgrade head
  ./start.sh help                       显示本帮助

start / restart 选项:
  --host HOST           API 绑定地址，默认 0.0.0.0
  --port PORT           API 端口，默认 8000
  --no-migrate          跳过启动前的数据库迁移
  -f, --foreground      前台运行，Ctrl+C 同时停止两个进程

环境变量:
  CAE_PYTHON                指定 Python 解释器（默认优先使用 .venv/bin/python）
  CAE_RUNTIME_DIR           PID 与后台日志目录，默认 <项目>/.runtime
  CAE_API_HOST / CAE_API_PORT
  CAE_START_TIMEOUT_SECONDS 等待 API 就绪的秒数，默认 30
  CAE_STOP_TIMEOUT_SECONDS  等待进程退出的秒数，默认 45

说明:
  API 与 Worker 共用同一份 .env，均在项目根目录启动。
  只有当前没有服务运行时才会自动迁移，也可用 --no-migrate 关闭。
  停止顺序为 API → Worker；后台日志在 .runtime/api.out 与 .runtime/worker.out。
TEXT
}

# ---------------------------------------------------------------- helpers

resolve_python() {
  local candidate
  if [ -n "${CAE_PYTHON:-}" ]; then
    printf '%s\n' "$CAE_PYTHON"
    return 0
  fi
  for candidate in "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/venv/bin/python" python3.10 python3 python; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

preflight() {
  PY="$(resolve_python)" || die "未找到 Python 解释器；请先创建虚拟环境（python3.10 -m venv .venv）或设置 CAE_PYTHON"
  [ -f "$SCRIPT_DIR/main.py" ] || die "未找到 main.py，start.sh 必须放在项目根目录"
  [ -f "$SCRIPT_DIR/worker.py" ] || die "未找到 worker.py，start.sh 必须放在项目根目录"
  "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' ||
    die "需要 Python 3.10 及以上，当前为 $("$PY" -V 2>&1)"
  local missing
  missing="$(
    "$PY" - <<'PYCODE'
import importlib.util

names = ("uvicorn", "fastapi", "pydantic_settings", "sqlalchemy", "alembic", "loguru")
print(" ".join(name for name in names if importlib.util.find_spec(name) is None))
PYCODE
  )"
  [ -z "$missing" ] || die "$PY 缺少依赖: $missing（请先 pip install -r requirements.txt）"
  [ -f "$SCRIPT_DIR/.env" ] ||
    warn "未找到 .env，将使用代码默认配置；可执行 cp .env.example .env 后按需修改"
  mkdir -p "$RUNTIME_DIR" || die "无法创建运行目录 $RUNTIME_DIR"
  [ -w "$RUNTIME_DIR" ] || die "运行目录不可写: $RUNTIME_DIR"
}

read_pid() {
  local file="$1" pid
  [ -f "$file" ] || return 1
  pid="$(tr -d '[:space:]' <"$file" 2>/dev/null)" || return 1
  case "$pid" in '' | *[!0-9]*) return 1 ;; esac
  printf '%s\n' "$pid"
}

# Print the PID when it is alive and still belongs to our service (guards against PID reuse).
alive_pid() {
  local file="$1" pattern="$2" pid
  pid="$(read_pid "$file")" || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  if [ -r "/proc/$pid/cmdline" ]; then
    tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q -- "$pattern" || return 1
  fi
  printf '%s\n' "$pid"
}

# Start a detached process, remember its PID, and append stdout/stderr to one log file.
spawn() {
  local pidfile="$1" logfile="$2"
  shift 2
  : >"$logfile"
  nohup "$@" >>"$logfile" 2>&1 </dev/null &
  local pid=$!
  printf '%s\n' "$pid" >"$pidfile"
  printf '%s\n' "$pid"
}

# Terminate a process and its descendants; used as the last resort after SIGTERM timed out.
kill_tree() {
  local pid="$1" sig="$2" child
  if command -v pgrep >/dev/null 2>&1; then
    for child in $(pgrep -P "$pid" 2>/dev/null); do
      kill_tree "$child" "$sig"
    done
  fi
  kill -"$sig" "$pid" 2>/dev/null
  return 0
}

api_probe_host() {
  case "$API_HOST" in
    0.0.0.0 | "*" | "::") printf '127.0.0.1' ;;
    *) printf '%s' "$API_HOST" ;;
  esac
}

health_url() { printf 'http://%s:%s/healthz' "$(api_probe_host)" "$API_PORT"; }

# Print the /healthz body on success; empty output and non-zero exit when unreachable.
health_body() {
  "$PY" - "$(health_url)" <<'PYCODE'
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
        sys.stdout.write(response.read().decode("utf-8", "replace").strip())
except Exception:
    raise SystemExit(1)
PYCODE
}

health_ok() { [ -n "$(health_body 2>/dev/null)" ]; }

process_uptime() { ps -o etime= -p "$1" 2>/dev/null | tr -d ' '; }

stale_pid_file() {
  local file="$1" name="$2"
  if [ -f "$file" ] && ! alive_pid "$file" "" >/dev/null 2>&1; then
    warn "清理 $name 的残留 PID 文件 $file"
    rm -f -- "$file"
  fi
}

# ---------------------------------------------------------------- subcommands

run_migration() {
  [ -f "$SCRIPT_DIR/alembic.ini" ] || die "未找到 alembic.ini，无法执行数据库迁移"
  log "执行数据库迁移: $PY -m alembic upgrade head"
  "$PY" -m alembic upgrade head || die "数据库迁移失败，未启动任何服务（需要跳过着用 --no-migrate）"
  "$PY" -m alembic current || true
}

start_api() {
  log "启动 API: $PY -m uvicorn main:create_app --factory --host $API_HOST --port $API_PORT"
  printf '%s %s\n' "$API_HOST" "$API_PORT" >"$API_ADDR_FILE"
  local pid
  pid="$(spawn "$API_PID_FILE" "$API_LOG" "$PY" -m uvicorn main:create_app --factory --host "$API_HOST" --port "$API_PORT")"
  local ready=0 deadline=$((SECONDS + START_TIMEOUT))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      warn "API 进程已退出，日志尾部（$API_LOG）:"
      tail -n 20 "$API_LOG" >&2 || true
      rm -f -- "$API_PID_FILE" "$API_ADDR_FILE"
      return 1
    fi
    if health_ok; then
      ready=1
      break
    fi
    sleep 0.2
  done
  if [ "$ready" -ne 1 ]; then
    warn "等待 API 就绪超时（${START_TIMEOUT}s），日志尾部（$API_LOG）:"
    tail -n 20 "$API_LOG" >&2 || true
    warn "API 进程 pid=$pid 仍在运行但未就绪；请用 ./start.sh stop 清理，或查看 ./start.sh logs api -f"
    return 1
  fi
  log "API 就绪 pid=$pid  $(health_url)"
  return 0
}

start_worker() {
  log "启动 Worker: $PY worker.py"
  local pid
  pid="$(spawn "$WORKER_PID_FILE" "$WORKER_LOG" "$PY" worker.py)"
  sleep 2
  if ! kill -0 "$pid" 2>/dev/null; then
    warn "Worker 启动后立即退出，日志尾部（$WORKER_LOG）:"
    tail -n 20 "$WORKER_LOG" >&2 || true
    if grep -q "已有 Worker 运行" "$WORKER_LOG" 2>/dev/null; then
      warn "检测到重复 Worker：数据库锁已被其它 Worker 占用"
    fi
    rm -f -- "$WORKER_PID_FILE"
    return 1
  fi
  log "Worker 运行中 pid=$pid"
  return 0
}

run_foreground() {
  log "前台模式：Ctrl+C 将同时停止 API 与 Worker"
  "$PY" -m uvicorn main:create_app --factory --host "$API_HOST" --port "$API_PORT" &
  local api_pid=$!
  "$PY" worker.py &
  local worker_pid=$!
  local interrupted=0
  trap 'interrupted=1; kill -TERM "$api_pid" "$worker_pid" 2>/dev/null' INT TERM
  wait -n "$api_pid" "$worker_pid" || true
  if [ "$interrupted" = 1 ]; then
    log "收到中断信号，已停止 API 与 Worker"
  else
    log "某个进程已退出，正在停止另一个"
  fi
  kill -TERM "$api_pid" "$worker_pid" 2>/dev/null
  wait "$api_pid" "$worker_pid" 2>/dev/null
  return 0
}

cmd_start() {
  preflight
  stale_pid_file "$API_PID_FILE" API
  stale_pid_file "$WORKER_PID_FILE" Worker

  local api_pid worker_pid
  api_pid="$(alive_pid "$API_PID_FILE" uvicorn)" || api_pid=""
  worker_pid="$(alive_pid "$WORKER_PID_FILE" worker.py)" || worker_pid=""
  if [ -n "$api_pid" ]; then
    log "API 已在运行 pid=$api_pid，跳过启动"
  fi
  if [ -n "$worker_pid" ]; then
    log "Worker 已在运行 pid=$worker_pid，跳过启动"
  fi
  if [ -n "$api_pid" ] && [ -n "$worker_pid" ]; then
    log "API 与 Worker 均已在运行；如需重启请执行 ./start.sh restart"
    return 0
  fi

  if [ -n "$api_pid" ] || [ -n "$worker_pid" ]; then
    if [ "$DO_MIGRATE" = 1 ]; then
      warn "已有服务在运行，跳过自动迁移以免并发写库（需要时先 ./start.sh stop，再 ./start.sh migrate）"
    fi
    if [ "$FOREGROUND" = 1 ]; then
      die "已有服务在运行，无法进入前台模式；请先 ./start.sh stop"
    fi
  elif [ "$DO_MIGRATE" = 1 ]; then
    run_migration
  fi

  if [ "$FOREGROUND" = 1 ]; then
    run_foreground
    return $?
  fi

  local rc=0
  if [ -z "$api_pid" ]; then
    start_api || rc=1
  fi
  if [ "$rc" -eq 0 ] && [ -z "$worker_pid" ]; then
    start_worker || rc=1
  fi
  if [ "$rc" -ne 0 ]; then
    warn "启动未完成；可用 ./start.sh status 查看状态、./start.sh logs 查看日志、./start.sh stop 清理"
    return 1
  fi

  log "启动完成"
  log "  API   $(health_url)  文档 http://$(api_probe_host):$API_PORT/docs  日志 $API_LOG"
  log "  Worker 日志 $WORKER_LOG"
  log "  停止  ./start.sh stop    状态  ./start.sh status"
  return 0
}

stop_service() {
  local name="$1" pidfile="$2" pattern="$3" pid
  pid="$(alive_pid "$pidfile" "$pattern")" || {
    if [ -f "$pidfile" ]; then
      warn "清理 $name 的残留 PID 文件 $pidfile"
    fi
    rm -f -- "$pidfile"
    log "$name 未在运行"
    return 0
  }
  log "停止 $name pid=$pid ..."
  kill -TERM "$pid" 2>/dev/null
  local deadline=$((SECONDS + STOP_TIMEOUT))
  while [ "$SECONDS" -lt "$deadline" ] && kill -0 "$pid" 2>/dev/null; do
    sleep 0.2
  done
  if kill -0 "$pid" 2>/dev/null; then
    warn "$name 在 ${STOP_TIMEOUT}s 内未退出，发送 SIGKILL（含子进程）"
    kill_tree "$pid" KILL
    sleep 0.5
  fi
  if kill -0 "$pid" 2>/dev/null; then
    warn "$name pid=$pid 仍未退出，请手动检查（PID 文件保留在 $pidfile）"
    return 1
  fi
  rm -f -- "$pidfile"
  log "$name 已停止"
  return 0
}

cmd_stop() {
  local rc=0
  # Stop the API first so no new task is accepted, then the Worker (interrupted runs are
  # marked unknown by worker.py itself).
  stop_service "API" "$API_PID_FILE" uvicorn || rc=1
  stop_service "Worker" "$WORKER_PID_FILE" worker.py || rc=1
  return "$rc"
}

cmd_restart() {
  local rc=0
  cmd_stop || rc=1
  if [ "$rc" -ne 0 ]; then
    warn "停止未完全成功，已中止重启"
    return 1
  fi
  cmd_start
}

cmd_status() {
  PY="$(resolve_python 2>/dev/null)" || PY=""
  local api_pid worker_pid rc=0 api_uptime worker_uptime body
  api_pid="$(alive_pid "$API_PID_FILE" uvicorn)" || api_pid=""
  worker_pid="$(alive_pid "$WORKER_PID_FILE" worker.py)" || worker_pid=""
  if [ -n "$api_pid" ] && [ -f "$API_ADDR_FILE" ]; then
    # Report the address the running API was actually started with, not the current env.
    read -r API_HOST API_PORT <"$API_ADDR_FILE" || true
  fi
  if [ -n "$api_pid" ]; then
    api_uptime="$(process_uptime "$api_pid")"
    if [ -n "$api_uptime" ]; then
      printf '[API]    运行中 pid=%s 已运行=%s  http://%s:%s/docs\n' \
        "$api_pid" "$api_uptime" "$(api_probe_host)" "$API_PORT"
    else
      printf '[API]    运行中 pid=%s  http://%s:%s/docs\n' \
        "$api_pid" "$(api_probe_host)" "$API_PORT"
    fi
  else
    printf '[API]    未运行\n'
    rc=1
  fi
  if [ -n "$worker_pid" ]; then
    worker_uptime="$(process_uptime "$worker_pid")"
    if [ -n "$worker_uptime" ]; then
      printf '[WORKER] 运行中 pid=%s 已运行=%s  日志=%s\n' \
        "$worker_pid" "$worker_uptime" "$WORKER_LOG"
    else
      printf '[WORKER] 运行中 pid=%s  日志=%s\n' "$worker_pid" "$WORKER_LOG"
    fi
  else
    printf '[WORKER] 未运行\n'
    rc=1
  fi
  if [ -n "$PY" ]; then
    body="$(health_body 2>/dev/null)" || body=""
    if [ -n "$body" ]; then
      printf '[健康]   %s → %s\n' "$(health_url)" "$body"
    else
      printf '[健康]   %s 无响应\n' "$(health_url)"
    fi
  else
    printf '[健康]   未找到 Python 解释器，跳过健康检查\n'
  fi
  printf '[运行目录] %s\n' "$RUNTIME_DIR"
  return "$rc"
}

cmd_logs() {
  local files=()
  case "$LOG_TARGET" in
    api) files=("$API_LOG") ;;
    worker) files=("$WORKER_LOG") ;;
    both) files=("$API_LOG" "$WORKER_LOG") ;;
    *) die "logs 目标只能是 api、worker 或 both" ;;
  esac
  local existing=() file
  for file in "${files[@]}"; do
    if [ -f "$file" ]; then
      existing+=("$file")
    else
      warn "$file 不存在（该服务可能还没启动过）"
    fi
  done
  [ "${#existing[@]}" -gt 0 ] || return 1
  if [ "$FOLLOW" = 1 ]; then
    tail -n "$LOG_LINES" -F "${existing[@]}"
  else
    tail -n "$LOG_LINES" "${existing[@]}"
  fi
}

cmd_migrate() {
  preflight
  local api_pid worker_pid
  api_pid="$(alive_pid "$API_PID_FILE" uvicorn)" || api_pid=""
  worker_pid="$(alive_pid "$WORKER_PID_FILE" worker.py)" || worker_pid=""
  if [ -n "$api_pid" ] || [ -n "$worker_pid" ]; then
    die "API 或 Worker 正在运行，为安全起见请先 ./start.sh stop 再迁移"
  fi
  run_migration
}

# ---------------------------------------------------------------- entry point

COMMAND="start"
if [ "$#" -gt 0 ] && [[ "$1" != -* ]]; then
  COMMAND="$1"
  shift
fi

case "$COMMAND" in
  start | stop | restart | status | logs | migrate | help) ;;
  *) die "未知命令: $COMMAND（可用: start | stop | restart | status | logs | migrate | help）" ;;
esac

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host)
      [ "$#" -ge 2 ] || die "--host 需要一个参数"
      API_HOST="$2"
      shift 2
      ;;
    --host=*)
      API_HOST="${1#*=}"
      shift
      ;;
    --port)
      [ "$#" -ge 2 ] || die "--port 需要一个参数"
      API_PORT="$2"
      shift 2
      ;;
    --port=*)
      API_PORT="${1#*=}"
      shift
      ;;
    --no-migrate)
      DO_MIGRATE=0
      shift
      ;;
    --foreground)
      FOREGROUND=1
      shift
      ;;
    -f)
      if [ "$COMMAND" = "logs" ]; then FOLLOW=1; else FOREGROUND=1; fi
      shift
      ;;
    --follow)
      FOLLOW=1
      shift
      ;;
    -n)
      [ "$#" -ge 2 ] || die "-n 需要一个参数"
      LOG_LINES="$2"
      shift 2
      ;;
    -h | --help)
      COMMAND="help"
      shift
      ;;
    api | worker | both)
      LOG_TARGET="$1"
      shift
      ;;
    *)
      die "未知参数: $1（使用 ./start.sh help 查看用法）"
      ;;
  esac
done

case "$API_PORT" in
  '' | *[!0-9]*) die "--port 必须是数字，当前为 '$API_PORT'" ;;
esac
case "$START_TIMEOUT" in '' | *[!0-9]*) START_TIMEOUT=30 ;; esac
case "$STOP_TIMEOUT" in '' | *[!0-9]*) STOP_TIMEOUT=45 ;; esac
LOG_LINES="${LOG_LINES:-50}"

case "$COMMAND" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  status) cmd_status ;;
  logs) cmd_logs ;;
  migrate) cmd_migrate ;;
  help) usage ;;
esac
exit $?
