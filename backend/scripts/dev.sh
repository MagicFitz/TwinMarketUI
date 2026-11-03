#!/usr/bin/env bash
# 一键启动本地开发环境：市场数据服务 + FastAPI 仿真后端 + Studio 前端（Vite）。
# Ctrl-C 同时停止全部；任一进程退出时其余进程也会被停止。
#
#   backend/scripts/dev.sh
#   MDS_PORT=8200 BACKEND_PORT=8800 FRONTEND_PORT=5200 backend/scripts/dev.sh
#
# 兼容 macOS 自带的 bash 3.2。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_ROOT"

# 可通过同名环境变量覆盖。
HOST="${HOST:-127.0.0.1}"
MDS_PORT="${MDS_PORT:-8100}"
BACKEND_PORT="${BACKEND_PORT:-8766}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
PYTHON="${PYTHON:-.venv/bin/python}"
BACKEND_READY_TIMEOUT="${BACKEND_READY_TIMEOUT:-30}"

fail() { echo "[dev] $*" >&2; exit 1; }

port_in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# 给每行输出加前缀，便于区分两路日志；fflush 保证实时显示。
prefix() { awk -v tag="$1" '{ print tag " " $0; fflush() }'; }

# ---------------------------------------------------------------- 预检查
[[ -x "$PYTHON" ]] || fail "找不到 Python 解释器 $PYTHON；请先在 backend/ 下创建 .venv 并安装 requirements.txt"
"$PYTHON" -c "import uvicorn, fastapi" 2>/dev/null || fail "$PYTHON 中缺少 uvicorn/fastapi；请执行 $PYTHON -m pip install -r server/requirements.txt"
command -v npm >/dev/null || fail "找不到 npm；请先安装 Node.js"
port_in_use "$MDS_PORT" && fail "端口 $MDS_PORT 已被占用（市场数据服务）；可用 MDS_PORT=... 指定其他端口"
port_in_use "$BACKEND_PORT" && fail "端口 $BACKEND_PORT 已被占用（后端）；可用 BACKEND_PORT=... 指定其他端口"
port_in_use "$FRONTEND_PORT" && fail "端口 $FRONTEND_PORT 已被占用（前端）；可用 FRONTEND_PORT=... 指定其他端口"
if [[ ! -d ../frontend/node_modules ]]; then
  echo "[dev] 首次运行，安装 Studio 依赖…"
  (cd ../frontend && npm install)
fi

# ---------------------------------------------------------------- 进程管理
# set -m 让每个后台任务拥有独立进程组：Ctrl-C 只发给脚本本身，
# 由 cleanup 按进程组停止（包括 npm 派生出的 node 子进程）。
set -m
MDS_PID=""
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  trap - INT TERM EXIT
  echo
  echo "[dev] 正在停止…"
  for pid in "$FRONTEND_PID" "$BACKEND_PID" "$MDS_PID"; do
    [[ -n "$pid" ]] && kill -TERM -- "-$pid" 2>/dev/null || true
  done
  # 给进程最多 5 秒正常退出，之后强制结束。
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    alive=0
    for pid in "$FRONTEND_PID" "$BACKEND_PID" "$MDS_PID"; do
      [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && alive=1
    done
    [[ "$alive" == 0 ]] && break
    sleep 0.5
  done
  for pid in "$FRONTEND_PID" "$BACKEND_PID" "$MDS_PID"; do
    [[ -n "$pid" ]] && kill -KILL -- "-$pid" 2>/dev/null || true
  done
  echo "[dev] 已停止"
}
trap cleanup INT TERM EXIT

# ------------------------------------------------------- 启动市场数据服务
# 首次启动会把 data/ 中已有的行情、最新快照和旧新闻导入 mds_data/。
echo "[dev] 启动市场数据服务 http://$HOST:$MDS_PORT"
"$PYTHON" -m market_service serve --host "$HOST" --port "$MDS_PORT" \
  > >(prefix "[market]") 2>&1 &
MDS_PID=$!
disown "$MDS_PID"

# ---------------------------------------------------------------- 启动后端
echo "[dev] 启动后端 http://$HOST:$BACKEND_PORT"
TWINMARKET_MDS_URL="http://$HOST:$MDS_PORT" \
  "$PYTHON" -m uvicorn server.app.main:app --host "$HOST" --port "$BACKEND_PORT" \
  > >(prefix "[backend]") 2>&1 &
BACKEND_PID=$!
disown "$BACKEND_PID"  # 不进入作业表，停止时 bash 不再打印 "Terminated"

# 等待健康检查通过，再启动前端，避免前端首屏请求失败。
waited=0
until curl -fs "http://$HOST:$BACKEND_PORT/health/live" >/dev/null 2>&1; do
  kill -0 "$BACKEND_PID" 2>/dev/null || fail "后端启动失败，请查看上方 [backend] 日志"
  (( waited >= BACKEND_READY_TIMEOUT * 2 )) && fail "后端 ${BACKEND_READY_TIMEOUT} 秒内未就绪"
  sleep 0.5
  waited=$((waited + 1))
done
echo "[dev] 后端已就绪"

waited=0
until curl -fs "http://$HOST:$MDS_PORT/health/live" >/dev/null 2>&1; do
  kill -0 "$MDS_PID" 2>/dev/null || fail "市场数据服务启动失败，请查看上方 [market] 日志"
  (( waited >= BACKEND_READY_TIMEOUT * 2 )) && fail "市场数据服务 ${BACKEND_READY_TIMEOUT} 秒内未就绪"
  sleep 0.5
  waited=$((waited + 1))
done
echo "[dev] 市场数据服务已就绪"

# ---------------------------------------------------------------- 启动前端
echo "[dev] 启动 Studio 前端 http://$HOST:$FRONTEND_PORT/studio/"
(cd ../frontend && TWINMARKET_API_URL="http://$HOST:$BACKEND_PORT" TWINMARKET_MDS_URL="http://$HOST:$MDS_PORT" \
  exec npx vite --host "$HOST" --port "$FRONTEND_PORT" --strictPort) \
  > >(prefix "[studio] ") 2>&1 &
FRONTEND_PID=$!
disown "$FRONTEND_PID"

echo "[dev] 打开 http://$HOST:$FRONTEND_PORT/studio/ ；按 Ctrl-C 停止"

# 任一进程退出就结束脚本（bash 3.2 没有 wait -n，改为轮询）。
while kill -0 "$MDS_PID" 2>/dev/null && kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done
kill -0 "$MDS_PID" 2>/dev/null || echo "[dev] 市场数据服务已退出" >&2
kill -0 "$BACKEND_PID" 2>/dev/null || echo "[dev] 后端已退出" >&2
kill -0 "$FRONTEND_PID" 2>/dev/null || echo "[dev] 前端已退出" >&2
exit 1
