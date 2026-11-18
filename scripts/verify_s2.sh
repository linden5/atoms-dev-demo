#!/usr/bin/env bash
# S2 验收：FastAPI 是否真的接管了服务（/health 可用 + web/ 仍可访问 + 单 worker）
# 用法：bash scripts/verify_s2.sh                        # 本机（deploy/verify.env 的 LOCAL_BASE_URL）
#       TARGET=public bash scripts/verify_s2.sh          # 公网（deploy/verify.env 的 PUBLIC_BASE_URL）
#       BASE_URL=http://IP:8000 bash scripts/verify_s2.sh # 临时指定，优先级最高
set -uo pipefail

# 地址来源优先级：命令行 BASE_URL > 按 TARGET 取 verify.env 里的地址 > 本机 8000
if [ -z "${BASE_URL:-}" ]; then
  VERIFY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  # shellcheck disable=SC1091
  [ -f "$VERIFY_ROOT/deploy/verify.env" ] && . "$VERIFY_ROOT/deploy/verify.env"

  case "${TARGET:-local}" in
    local)
      BASE_URL="${LOCAL_BASE_URL:-http://127.0.0.1:8000}"
      ;;
    public)
      if [ -z "${PUBLIC_BASE_URL:-}" ]; then
        printf 'TARGET=public，但 deploy/verify.env 里没有 PUBLIC_BASE_URL\n' >&2
        exit 2
      fi
      BASE_URL="$PUBLIC_BASE_URL"
      ;;
    *)
      printf 'TARGET 只能是 local 或 public（当前：%s）\n' "$TARGET" >&2
      exit 2
      ;;
  esac
fi
BASE_URL="${BASE_URL%/}"

PASS=0
FAIL=0

ok() { PASS=$((PASS + 1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
no() { FAIL=$((FAIL + 1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }

fetch() { curl -fsS --max-time 5 "$1" 2>/dev/null; }

expect_status() {
  local label="$1" url="$2" want="$3"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" || true)"
  if [ "$code" = "$want" ]; then
    ok "$label"
  else
    no "$label（返回 ${code:-无响应}，期望 $want）"
  fi
}

expect_contains() {
  local label="$1" url="$2" needle="$3"
  local body
  body="$(fetch "$url")"
  if [ -z "$body" ]; then
    no "$label（请求失败：$url）"
    return
  fi
  if printf '%s' "$body" | grep -qF -- "$needle"; then
    ok "$label"
  else
    no "$label（未找到：$needle）"
  fi
}

printf '\nS2 验收 · %s\n\n' "$BASE_URL"

# 1. 后端：/health 是这一步新增的唯一接口
expect_status "/health 返回 200" "${BASE_URL}/health" "200"
expect_contains "/health 报告 status=ok" "${BASE_URL}/health" '"status":"ok"'
expect_contains "/health 报告当前步骤 s2" "${BASE_URL}/health" '"step":"s2"'
expect_contains "/health 带版本号" "${BASE_URL}/health" '"version"'
expect_contains "/health 带运行时长" "${BASE_URL}/health" '"uptime_s"'

# 2. 证明是 ASGI 服务而不是 http.server：HEAD 可用，且成功响应的头里应有 uvicorn
head_code="$(curl -s -o /dev/null -w '%{http_code}' -I --max-time 5 "${BASE_URL}/health" 2>/dev/null || true)"
if [ "$head_code" = "200" ]; then
  ok "/health 支持 HEAD（运维探活常用）"
else
  no "/health 的 HEAD 返回 ${head_code:-无响应}（期望 200）"
fi
headers="$(curl -s -D - -o /dev/null --max-time 5 "${BASE_URL}/health" 2>/dev/null || true)"
if printf '%s' "$headers" | grep -qi 'server: uvicorn'; then
  ok "响应头证明由 uvicorn 提供服务"
else
  no "响应头未发现 server: uvicorn（可能还是 S1 的 http.server）"
fi

# 3. 静态托管：web/ 由 FastAPI 挂载提供，S1 的页面不能丢
expect_status "首页返回 200" "${BASE_URL}/" "200"
expect_contains "页面标题未被破坏" "${BASE_URL}/" "Atoms Demo"
expect_contains "存在预览 iframe" "${BASE_URL}/" 'id="preview-frame"'
expect_contains "iframe 仍启用 sandbox 隔离" "${BASE_URL}/" 'sandbox="allow-scripts"'
expect_status "样式表可访问" "${BASE_URL}/styles.css" "200"
expect_status "脚本可访问" "${BASE_URL}/app.js" "200"
expect_contains "前端已接入 /health 探测" "${BASE_URL}/app.js" "probeHealth"

# 4. 路由隔离：未实现的接口返回 404，而不是被静态托管吞成 200
expect_status "未实现接口返回 404" "${BASE_URL}/api/tasks" "404"

printf '\n结果：%d 通过 / %d 失败\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
printf 'S2 验收通过。\n\n'
