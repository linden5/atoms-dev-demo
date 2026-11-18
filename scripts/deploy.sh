#!/usr/bin/env bash
# 统一部署入口：拉代码 -> 同步依赖（如果有） -> 更新 systemd unit -> 重启并探活
# 用法：./scripts/deploy.sh
# 可覆盖的环境变量：APP_DIR SERVICE PORT BASE_URL
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE="${SERVICE:-atoms}"
PORT="${PORT:-8000}"
BASE_URL="${BASE_URL:-http://127.0.0.1:${PORT}}"

log() { printf '[deploy] %s\n' "$*"; }
fail() { printf '[deploy] 失败：%s\n' "$*" >&2; exit 1; }

cd "$APP_DIR"

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    log "拉取最新代码"
    git pull --ff-only
  else
    log "当前分支没有上游（首次部署或未 push），跳过 git pull"
  fi
fi

if [ -f requirements.txt ] && [ -x .venv/bin/python ]; then
  if command -v uv >/dev/null 2>&1; then
    log "同步依赖"
    uv pip install -r requirements.txt
  else
    log "未找到 uv，跳过依赖同步"
  fi
fi

if [ -f deploy/atoms.service ] && command -v systemctl >/dev/null 2>&1; then
  SUDO=""
  if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
  fi
  log "更新 systemd unit 并重启 ${SERVICE}"
  $SUDO cp deploy/atoms.service "/etc/systemd/system/${SERVICE}.service"
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable "$SERVICE" >/dev/null 2>&1 || true
  $SUDO systemctl restart "$SERVICE"
else
  log "未检测到 systemd 或 unit 文件，跳过服务重启"
fi

for _ in $(seq 1 15); do
  if curl -fsS -o /dev/null --max-time 2 "${BASE_URL}/"; then
    log "服务已就绪：${BASE_URL}"
    exit 0
  fi
  sleep 1
done

fail "服务未在 15 秒内就绪，请检查 journalctl -u ${SERVICE} -n 50"
