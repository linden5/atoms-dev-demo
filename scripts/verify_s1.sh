#!/usr/bin/env bash
# S1 验收：静态页面可访问，且资源与交互能力齐备
# 用法：bash scripts/verify_s1.sh                        # 本机（deploy/verify.env 的 LOCAL_BASE_URL）
#       TARGET=public bash scripts/verify_s1.sh          # 公网（deploy/verify.env 的 PUBLIC_BASE_URL）
#       BASE_URL=http://IP:8000 bash scripts/verify_s1.sh # 临时指定，优先级最高
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

printf '\nS1 验收 · %s\n\n' "$BASE_URL"

code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${BASE_URL}/" || true)"
if [ "$code" = "200" ]; then
  ok "首页返回 200"
else
  no "首页返回 ${code:-无响应}（期望 200）"
fi

expect_contains "页面标题包含 Atoms Demo" "${BASE_URL}/" "Atoms Demo"
expect_contains "存在需求输入框" "${BASE_URL}/" 'id="requirement-input"'
expect_contains "存在预览 iframe" "${BASE_URL}/" 'id="preview-frame"'
expect_contains "iframe 启用 sandbox 隔离" "${BASE_URL}/" 'sandbox="allow-scripts"'
expect_contains "存在任务列表容器" "${BASE_URL}/" 'id="task-list"'
expect_contains "存在示例 prompt 快捷入口" "${BASE_URL}/" 'data-prompt='

expect_contains "样式表可访问且含设计变量" "${BASE_URL}/styles.css" "--accent-0"
# S1 的演示数据在 S3 被真实接口取代，因此这里只断言"页面脚本能提交需求"，
# 不写死 DEMO_TASKS：否则 S3 起的全量回归会误报。
expect_contains "脚本可访问且包含提交逻辑" "${BASE_URL}/app.js" "/api/generate"
expect_contains "脚本包含失败态处理" "${BASE_URL}/app.js" "errorBanner"

printf '\n结果：%d 通过 / %d 失败\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
printf 'S1 验收通过。\n\n'
