#!/usr/bin/env bash
# S8 验收：真实 LLM 生成 + 发布/分享闭环 + 版本叙事
# 用法：bash scripts/verify_s8.sh
#       BASE_URL=http://IP:8000 bash scripts/verify_s8.sh
#       DEEP=1 bash scripts/verify_s8.sh     # 额外跑"另一个应用抢同一地址 → 409"（会多生成一次，真模型下约 1 分钟）
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -z "${BASE_URL:-}" ]; then
  # shellcheck disable=SC1091
  [ -f "$ROOT_DIR/deploy/verify.env" ] && . "$ROOT_DIR/deploy/verify.env"
  BASE_URL="${LOCAL_BASE_URL:-http://127.0.0.1:8000}"
fi
BASE_URL="${BASE_URL%/}"
DEEP="${DEEP:-0}"

PASS=0
FAIL=0
SKIP=0

ok() { PASS=$((PASS + 1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
no() { FAIL=$((FAIL + 1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
skip() { SKIP=$((SKIP + 1)); printf '  \033[33mSKIP\033[0m  %s\n' "$1"; }
info() { printf '  \033[36m···\033[0m  %s\n' "$1"; }

fetch() { curl -fsS --max-time 8 "$1" 2>/dev/null; }
status_of() { curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$@" 2>/dev/null || true; }
header_of() { curl -sS -o /dev/null -D - --max-time 8 "$@" 2>/dev/null | tr -d '\r'; }

json_field() {
  printf '%s' "$1" | grep -o "\"$2\":\"[^\"]*\"" | head -n 1 | cut -d'"' -f4
}
json_num() {
  printf '%s' "$1" | grep -o "\"$2\":[0-9]\+" | head -n 1 | cut -d':' -f2
}

post_json() {
  local path="$1" body="$2" tmp response code
  tmp="$(mktemp)"
  printf '%s' "$body" >"$tmp"
  response="$(curl -sS --max-time 20 -X POST "${BASE_URL}${path}" \
    -H 'Content-Type: application/json' --data-binary "@${tmp}" 2>/dev/null)"
  rm -f "$tmp"
  printf '%s' "$response"
}
post_json_code() {
  local path="$1" body="$2" tmp code
  tmp="$(mktemp)"
  printf '%s' "$body" >"$tmp"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -X POST "${BASE_URL}${path}" \
    -H 'Content-Type: application/json' --data-binary "@${tmp}" 2>/dev/null || true)"
  rm -f "$tmp"
  printf '%s' "$code"
}

wait_succeeded() {
  local task_id="$1" started detail
  started="$(date +%s)"
  while :; do
    detail="$(fetch "${BASE_URL}/api/tasks/${task_id}")"
    case "$(json_field "$detail" status)" in
      succeeded | failed | cancelled) break ;;
    esac
    [ $(($(date +%s) - started)) -ge 240 ] && break
    sleep 0.5
  done
  printf '%s' "$detail"
}

printf '\nS8 验收 · %s\n\n' "$BASE_URL"

# ---------- 1. 服务与引擎 ----------
health="$(fetch "${BASE_URL}/health")"
if [ -z "$health" ]; then
  printf '  \033[31mFAIL\033[0m  /health 无响应，服务未启动？\n\nS8 验收中止。\n\n'
  exit 1
fi
ok "/health 可用"

step="$(json_field "$health" step)"
step_num="${step#s}"
case "${step_num}" in
  '' | *[!0-9]*) no "步骤号无法解析（step=${step:-空}）" ;;
  *) [ "$step_num" -ge 8 ] && ok "当前步骤为 s${step_num}" || no "当前步骤是 s${step_num}，S8 尚未上线" ;;
esac

engine="$(json_field "$health" engine)"
mode="$(json_field "$health" engine_mode)"
model="$(json_field "$health" model)"
info "engine=${engine:-空} engine_mode=${mode:-空} model=${model:-空}"
case "$engine" in
  llm)
    ok "引擎为真实 LLM"
    [ -n "$model" ] && ok "回显了模型名（${model}）" || no "engine=llm 但未回显 model"
    ;;
  mock)
    skip "引擎是 MOCK，跳过真生成相关判定（演示兜底可接受，但交付前应为 llm）"
    ;;
  metagpt) info "引擎为 MetaGPT" ;;
  *) no "engine 字段异常：${engine:-空}" ;;
esac

# ---------- 2. 会话与提问 ----------
created="$(post_json '/api/sessions' '{}')"
session_id="$(json_field "$created" session_id)"
[ -n "$session_id" ] && ok "创建会话成功（${session_id}）" || no "创建会话失败（${created:-空}）"

clarify="$(post_json "/api/sessions/${session_id}/messages" '{"content":"做个网页"}')"
[ "$(json_field "$clarify" action)" = "clarify" ] \
  && ok "模糊需求触发追问（action=clarify）" || no "模糊需求未触发追问"

# ---------- 3. 一次真实生成 ----------
prompt='S8 验收：做一个待办清单，能增删、勾选完成，并在底部显示剩余数量'
generated="$(post_json "/api/sessions/${session_id}/messages" "{\"content\":\"${prompt}\"}")"
task_id="$(json_field "$generated" task_id)"
if [ "$(json_field "$generated" action)" = "generate" ] && [ -n "$task_id" ]; then
  ok "具体需求触发生成（task=${task_id}）"
else
  no "具体需求未触发生成（action=$(json_field "$generated" action)）"
fi

if [ -n "$task_id" ]; then
  detail="$(wait_succeeded "$task_id")"
  status="$(json_field "$detail" status)"
  [ "$status" = "succeeded" ] && ok "生成成功" || no "生成未成功（status=${status:-空}）"

  if printf '%s' "$detail" | grep -q '"path":"index.html"'; then
    ok "产物含 index.html"
  else
    no "产物缺少 index.html"
  fi

  vtitle="$(json_field "$detail" version_title)"
  [ -n "$vtitle" ] && ok "版本名已写入（${vtitle}）" || no "version_title 为空，版本卡片会没有名字"

  html="$(fetch "${BASE_URL}/api/tasks/${task_id}/files/index.html")"
  chars=$(printf '%s' "$html" | wc -c | tr -d ' ')
  if printf '%s' "$html" | grep -qi '<script' && [ "$chars" -gt 800 ]; then
    ok "产物是可交互页面（${chars} 字节，含 script）"
  else
    no "产物像空壳（${chars} 字节，script=$(printf '%s' "$html" | grep -ci '<script'))"
  fi

  headers="$(header_of "${BASE_URL}/api/tasks/${task_id}/files/index.html")"
  printf '%s' "$headers" | grep -qi 'content-security-policy' \
    && ok "预览页带独立 CSP" || no "预览页缺少 CSP"
  printf '%s' "$headers" | grep -qi 'content-type: text/html' \
    && ok "预览页 Content-Type 为 text/html" || no "预览页 Content-Type 异常"

  code="$(status_of "${BASE_URL}/api/tasks/${task_id}/files/../../app/main.py")"
  [ "$code" = "404" ] || [ "$code" = "403" ] \
    && ok "路径穿越被拦（${code}）" || no "路径穿越未被拦（${code}）"

  # ---------- 4. 第二轮：版本链 ----------
  second="$(post_json "/api/sessions/${session_id}/messages" '{"content":"把主色改成绿色，并加一个清空按钮"}')"
  task2="$(json_field "$second" task_id)"
  if [ -n "$task2" ]; then
    detail2="$(wait_succeeded "$task2")"
    [ "$(json_field "$detail2" status)" = "succeeded" ] && ok "第二轮生成成功" || no "第二轮生成失败"
    [ "$(json_field "$detail2" parent_id)" = "$task_id" ] \
      && ok "第二轮 parent 指向首版" || no "第二轮 parent=$(json_field "$detail2" parent_id)，期望 ${task_id}"

    versions="$(fetch "${BASE_URL}/api/tasks/${task2}/versions")"
    total="$(json_num "$versions" total)"
    [ "${total:-0}" -ge 2 ] && ok "版本链共 ${total} 个版本" || no "版本链异常（total=${total:-0}）"
  else
    no "第二轮未生成任务"
  fi

  # ---------- 5. 发布闭环 ----------
  slug="s8-$(date +%H%M%S)"
  pub="$(post_json "/api/tasks/${task_id}/publish" "{\"slug\":\"${slug}\"}")"
  got_slug="$(json_field "$pub" slug)"
  [ "$got_slug" = "$slug" ] && ok "发布成功（/p/${slug}）" || no "发布失败（${pub:-空}）"

  code="$(status_of "${BASE_URL}/p/${slug}")"
  [ "$code" = "200" ] && ok "公开链接可访问（200）" || no "公开链接返回 ${code:-无响应}"

  public_html="$(fetch "${BASE_URL}/p/${slug}")"
  printf '%s' "$public_html" | grep -qi '<script' \
    && ok "公开链接返回的是可交互页面" || no "公开链接内容异常"

  pub_headers="$(header_of "${BASE_URL}/p/${slug}")"
  printf '%s' "$pub_headers" | grep -qi 'x-frame-options: *deny' \
    && no "公开页带了 X-Frame-Options:DENY，无法被嵌入" || ok "公开页允许被嵌入"
  printf '%s' "$pub_headers" | grep -qi 'content-security-policy' \
    && ok "公开页带 CSP" || no "公开页缺少 CSP"

  of_task="$(fetch "${BASE_URL}/api/tasks/${task_id}/publish")"
  [ "$(json_field "$of_task" slug)" = "$slug" ] \
    && ok "任务可反查自己的公开地址" || no "任务查不到公开地址"

  code="$(post_json_code "/api/tasks/${task_id}/publish" '{"slug":"Bad Slug"}')"
  [ "$code" = "400" ] && ok "非法地址返回 400" || no "非法地址返回 ${code:-无响应}，期望 400"

  if [ -n "${task2:-}" ]; then
    upd="$(post_json "/api/tasks/${task2}/publish" "{\"slug\":\"${slug}\"}")"
    if printf '%s' "$upd" | grep -q '"updated":true'; then
      ok "同会话再发布 → 更新地址到新版本"
    else
      no "更新地址失败（${upd:-空}）"
    fi
  fi

  zip_code="$(status_of "${BASE_URL}/api/tasks/${task_id}/zip")"
  [ "$zip_code" = "200" ] && ok "导出 ZIP 可用" || no "导出 ZIP 返回 ${zip_code:-无响应}"

  # ---------- 6. 409 冲突（需要第二个应用，默认跳过） ----------
  if [ "$DEEP" = "1" ]; then
    other="$(post_json '/api/sessions' '{}')"
    other_id="$(json_field "$other" session_id)"
    other_task_json="$(post_json "/api/sessions/${other_id}/messages" \
      '{"content":"做一个记账本，能添加收入和支出，并显示当前结余"}')"
    other_task="$(json_field "$other_task_json" task_id)"
    wait_succeeded "$other_task" >/dev/null
    code="$(post_json_code "/api/tasks/${other_task}/publish" "{\"slug\":\"${slug}\"}")"
    [ "$code" = "409" ] && ok "别的应用抢占同一地址返回 409" || no "抢占地址返回 ${code:-无响应}，期望 409"
  else
    skip "409 抢占检查需要第二个应用，设 DEEP=1 开启"
  fi

  # ---------- 7. 取消发布 ----------
  code="$(status_of -X DELETE "${BASE_URL}/api/publishes/${slug}")"
  [ "$code" = "200" ] && ok "取消发布成功" || no "取消发布返回 ${code:-无响应}"
  code="$(status_of "${BASE_URL}/p/${slug}")"
  [ "$code" = "404" ] && ok "取消发布后公开链接 404" || no "取消发布后仍返回 ${code:-无响应}"
fi

# ---------- 8. 前端契约 ----------
home_html="$(fetch "${BASE_URL}/")"
for needle in 'id="publish-panel"' 'id="publish-btn"' 'id="share-panel"' 'id="preview-note"' \
  'data-tab="versions"' 'data-tab="messages"' 'id="history-search"'; do
  if printf '%s' "$home_html" | grep -qF "$needle"; then
    ok "页面包含 ${needle}"
  else
    no "页面缺少 ${needle}"
  fi
done

if printf '%s' "$home_html" | grep -qF 'sandbox="allow-scripts"' && \
   ! printf '%s' "$home_html" | grep -qF 'allow-same-origin'; then
  ok "预览 iframe 仍是 sandbox=allow-scripts（无 allow-same-origin）"
else
  no "预览 iframe 的 sandbox 配置被改动"
fi

main_headers="$(header_of "${BASE_URL}/")"
printf '%s' "$main_headers" | grep -qi 'content-security-policy' \
  && ok "主站仍带 CSP" || no "主站 CSP 丢了"
printf '%s' "$main_headers" | grep -qi 'x-content-type-options: *nosniff' \
  && ok "主站仍带 nosniff" || no "主站 nosniff 丢了"

printf '\n结果：%d 通过 / %d 失败 / %d 跳过\n' "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ] || exit 1
printf 'S8 验收通过。\n\n'
