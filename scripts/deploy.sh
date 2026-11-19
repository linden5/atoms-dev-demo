#!/usr/bin/env bash
# 统一部署入口：拉代码 -> 同步依赖（如果有） -> 渲染 systemd unit -> 重启并探活
#
# 多实例部署（同一台服务器按分支跑两套，目录/端口/服务/数据各自独立）：
#   INSTANCE=accept  验收环境：分支 main，目录 ~/atoms-dev-demo，     端口 8000，服务 atoms
#   INSTANCE=dev     自测环境：分支 dev，  目录 ~/atoms-dev-demo-dev，端口 8001，服务 atoms-dev
# 用法：
#   ./scripts/deploy.sh                    # 验收环境（默认，与旧行为一致）
#   INSTANCE=dev ./scripts/deploy.sh       # 自测环境
#   INSTANCE=dev APP_DIR=/srv/x PORT=9000 BRANCH=dev ./scripts/deploy.sh  # 逐项覆盖预设
# 数据隔离说明：SQLite（data/app.db）、产物（workspace/）、日志（logs/）都按代码目录相对定位，
# 两个实例必须是两个独立 clone（或 worktree），数据天然不串。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log() { printf '[deploy] %s\n' "$*"; }
fail() { printf '[deploy] 失败：%s\n' "$*" >&2; exit 1; }

INSTANCE="${INSTANCE:-accept}"
case "$INSTANCE" in
  accept) DEF_APP_DIR="$HOME/atoms-dev-demo";     DEF_SERVICE="atoms";     DEF_PORT="8000"; DEF_BRANCH="main" ;;
  dev)    DEF_APP_DIR="$HOME/atoms-dev-demo-dev"; DEF_SERVICE="atoms-dev"; DEF_PORT="8001"; DEF_BRANCH="dev" ;;
  *) fail "未知 INSTANCE=$INSTANCE（可选：accept | dev）" ;;
esac

# APP_DIR 预设：实例目录存在（服务器上已 clone）才用预设，否则退回仓库根（兼容本地直接跑）
if [ -z "${APP_DIR:-}" ]; then
  if [ -d "$DEF_APP_DIR/.git" ]; then APP_DIR="$DEF_APP_DIR"; else APP_DIR="$REPO_ROOT"; fi
fi
SERVICE="${SERVICE:-$DEF_SERVICE}"
PORT="${PORT:-$DEF_PORT}"
BRANCH="${BRANCH:-$DEF_BRANCH}"
BASE_URL="${BASE_URL:-http://127.0.0.1:${PORT}}"

log "实例=$INSTANCE 目录=$APP_DIR 分支=$BRANCH 服务=$SERVICE 端口=$PORT"

if [ ! -d "$APP_DIR/.git" ]; then
  fail "目录不是 git 仓库：$APP_DIR（首次部署请先 clone -b $BRANCH <仓库地址> $APP_DIR）"
fi
cd "$APP_DIR"

log "同步分支 $BRANCH"
git fetch origin "$BRANCH"
CUR_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CUR_BRANCH" != "$BRANCH" ]; then
  log "当前在 $CUR_BRANCH，切换到 $BRANCH"
  git checkout "$BRANCH"
fi
git pull --ff-only origin "$BRANCH"

if [ -f requirements.txt ]; then
  if [ ! -x .venv/bin/python ]; then
    log "未发现 .venv，尝试创建（Python 3.11）"
    if command -v uv >/dev/null 2>&1; then
      uv venv --python 3.11 .venv
    else
      python3.11 -m venv .venv
    fi
  fi
  if [ -x .venv/bin/python ]; then
    if command -v uv >/dev/null 2>&1; then
      log "同步依赖"
      # 显式指定解释器：deploy.sh 不激活 venv，uv 默认找不到环境会直接报错
      uv pip install --python .venv/bin/python -r requirements.txt
    else
      log "未找到 uv，跳过依赖同步"
    fi
  else
    fail "venv 创建失败：既没有 uv 也没有 python3.11"
  fi
fi

UNIT_SRC="$REPO_ROOT/deploy/atoms.service"
if [ -f "$UNIT_SRC" ] && command -v systemctl >/dev/null 2>&1; then
  SUDO=""
  if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
  fi
  log "渲染 systemd unit 并重启 ${SERVICE}"
  TMP_UNIT="$(mktemp)"
  sed -e "s|@SERVICE@|$SERVICE|g" -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@PORT@|$PORT|g" \
    "$UNIT_SRC" > "$TMP_UNIT"
  $SUDO cp "$TMP_UNIT" "/etc/systemd/system/${SERVICE}.service"
  rm -f "$TMP_UNIT"
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable "$SERVICE" >/dev/null 2>&1 || true
  $SUDO systemctl restart "$SERVICE"
else
  log "未检测到 systemd 或 unit 文件，跳过服务重启"
fi

for _ in $(seq 1 15); do
  if curl -fsS -o /dev/null --max-time 2 "${BASE_URL}/"; then
    log "服务已就绪：${BASE_URL}（journalctl -u ${SERVICE} -f）"
    exit 0
  fi
  sleep 1
done

fail "服务未在 15 秒内就绪，请检查 journalctl -u ${SERVICE} -n 50"
