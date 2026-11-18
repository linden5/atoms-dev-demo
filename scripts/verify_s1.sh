#!/usr/bin/env bash
# S1 验收：静态页面可访问，且资源与交互能力齐备
# 用法：./scripts/verify_s1.sh            # 默认 http://127.0.0.1:8000
#       BASE_URL=http://IP:8000 ./scripts/verify_s1.sh
set -uo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
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
expect_contains "脚本可访问且含演示数据" "${BASE_URL}/app.js" "DEMO_TASKS"
expect_contains "脚本包含生成模拟逻辑（提交可交互）" "${BASE_URL}/app.js" "simulateGeneration"
expect_contains "脚本包含失败态处理" "${BASE_URL}/app.js" "errorBanner"

printf '\n结果：%d 通过 / %d 失败\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
printf 'S1 验收通过。\n\n'
