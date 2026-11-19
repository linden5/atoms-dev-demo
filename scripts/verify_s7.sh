#!/usr/bin/env bash
# S7 验收：多轮对话（会话 / 消息 / 追问 / 上下文）
# 用法：bash scripts/verify_s7.sh
#       BASE_URL=http://IP:8000 bash scripts/verify_s7.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -z "${BASE_URL:-}" ]; then
  # shellcheck disable=SC1091
  [ -f "$ROOT_DIR/deploy/verify.env" ] && . "$ROOT_DIR/deploy/verify.env"
  BASE_URL="${LOCAL_BASE_URL:-http://127.0.0.1:8000}"
fi
BASE_URL="${BASE_URL%/}"

PASS=0
FAIL=0
SKIP=0

ok() { PASS=$((PASS + 1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
no() { FAIL=$((FAIL + 1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
skip() { SKIP=$((SKIP + 1)); printf '  \033[33mSKIP\033[0m  %s\n' "$1"; }
info() { printf '  \033[36m···\033[0m  %s\n' "$1"; }

fetch() { curl -fsS --max-time 8 "$1" 2>/dev/null; }
status_of() { curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$@" 2>/dev/null || true; }

json_field() {
  printf '%s' "$1" | grep -o "\"$2\":\"[^\"]*\"" | head -n 1 | cut -d'"' -f4
}

# 用文件传 body，避免中文在多级 shell 里被转码
post_json() {
  local path="$1" body="$2" tmp response
  tmp="$(mktemp)"
  printf '%s' "$body" >"$tmp"
  response="$(curl -sS --max-time 15 -X POST "${BASE_URL}${path}" \
    -H 'Content-Type: application/json' --data-binary "@${tmp}" 2>/dev/null)"
  rm -f "$tmp"
  printf '%s' "$response"
}

wait_succeeded() {
  local task_id="$1" started detail
  started="$(date +%s)"
  while :; do
    detail="$(fetch "${BASE_URL}/api/tasks/${task_id}")"
    [ "$(json_field "$detail" status)" = "succeeded" ] && break
    [ "$(json_field "$detail" status)" = "failed" ] && break
    [ $(($(date +%s) - started)) -ge 180 ] && break
    sleep 0.5
  done
  printf '%s' "$detail"
}

printf '\nS7 验收 · %s\n\n' "$BASE_URL"

# ---------- 1. 服务与步骤 ----------
health="$(fetch "${BASE_URL}/health")"
if [ -z "$health" ]; then
  printf '  \033[31mFAIL\033[0m  /health 无响应，服务未启动？\n\nS7 验收中止。\n\n'
  exit 1
fi
ok "/health 可用"

step="$(json_field "$health" step)"
step_num="${step#s}"
case "${step_num}" in
  '' | *[!0-9]*) no "步骤号无法解析（step=${step:-空}）" ;;
  *) [ "$step_num" -ge 7 ] && ok "当前步骤为 s${step_num}" || no "当前步骤是 s${step_num}，S7 尚未上线" ;;
esac

# ---------- 2. 建会话 ----------
created="$(post_json '/api/sessions' '{}')"
session_id="$(json_field "$created" session_id)"
if [ -n "$session_id" ]; then
  ok "创建会话成功（${session_id}）"
else
  no "创建会话失败（响应：${created:-空}）"
fi

if [ -n "$session_id" ]; then
  list="$(fetch "${BASE_URL}/api/sessions?limit=50")"
  if printf '%s' "$list" | grep -qF "\"$session_id\""; then
    ok "会话出现在列表中"
  else
    no "会话未出现在列表"
  fi

  # ---------- 3. 模糊需求 → 追问，不建任务 ----------
  clarify="$(post_json "/api/sessions/${session_id}/messages" '{"content":"做个网页"}')"
  action="$(json_field "$clarify" action)"
  if [ "$action" = "clarify" ]; then
    ok "模糊需求触发追问（action=clarify）"
  else
    no "模糊需求未触发追问（action=${action:-空}）"
  fi
  if printf '%s' "$clarify" | grep -q '"task_id":null'; then
    ok "追问不创建任务"
  else
    no "追问却创建了任务"
  fi

  # ---------- 4. 具体需求 → 建任务 ----------
  generated="$(post_json "/api/sessions/${session_id}/messages" \
    '{"content":"S7 验收：做一个番茄钟，25 分钟专注加 5 分钟休息，深色主题"}')"
  action="$(json_field "$generated" action)"
  task_id="$(json_field "$generated" task_id)"
  if [ "$action" = "generate" ] && [ -n "$task_id" ]; then
    ok "具体需求直接生成（task=${task_id}）"
  else
    no "具体需求未触发生成（action=${action:-空} task=${task_id:-空}）"
  fi

  if [ -n "$task_id" ]; then
    detail="$(wait_succeeded "$task_id")"
    [ "$(json_field "$detail" status)" = "succeeded" ] && ok "首轮任务生成成功" || no "首轮任务未成功"

    # 展示用的 prompt 必须是用户原话，不能是被拼接过的上下文
    prompt="$(json_field "$detail" prompt)"
    if [ "$prompt" = "S7 验收：做一个番茄钟，25 分钟专注加 5 分钟休息，深色主题" ]; then
      ok "任务 prompt 保留用户原话（未污染为上下文拼接）"
    else
      no "任务 prompt 被改写：${prompt}"
    fi

    # ---------- 5. 会话详情：消息顺序与任务快照 ----------
    session="$(fetch "${BASE_URL}/api/sessions/${session_id}")"
    expected="user text assistant question user text assistant artifact"
    actual="$(printf '%s' "$session" | grep -o '"role":"[a-z]*","kind":"[a-z]*"' \
      | sed 's/"role":"//; s/","kind":"/ /; s/"//g' | tr '\n' ' ' | sed 's/ *$//')"
    if [ "$actual" = "$expected" ]; then
      ok "消息顺序符合 user→question→user→artifact"
    else
      no "消息顺序异常（实际：${actual}）"
    fi

    if printf '%s' "$session" | grep -q '"entry":"index.html"'; then
      ok "会话详情内联了任务快照（含 entry）"
    else
      no "会话详情里没有可预览入口"
    fi

    # ---------- 6. 第二轮修改 → parent 指向首版 ----------
    second="$(post_json "/api/sessions/${session_id}/messages" \
      '{"content":"再加一个休息结束的提示音，其他保持不变"}')"
    task2="$(json_field "$second" task_id)"
    if [ -z "$task2" ]; then
      no "第二轮修改未生成任务（action=$(json_field "$second" action)）"
    else
      detail2="$(wait_succeeded "$task2")"
      parent="$(json_field "$detail2" parent_id)"
      if [ "$parent" = "$task_id" ]; then
        ok "第二轮任务的 parent 指向首版（版本链正确）"
      else
        no "第二轮 parent=${parent:-空}，期望 ${task_id}"
      fi
    fi
  fi

  # ---------- 7. 未知会话 404 ----------
  code="$(status_of "${BASE_URL}/api/sessions/not-exist-session")"
  [ "$code" = "404" ] && ok "未知会话返回 404" || no "未知会话返回 ${code:-无响应}，期望 404"

  code="$(status_of -X POST "${BASE_URL}/api/sessions/not-exist-session/messages" \
    -H 'Content-Type: application/json' -d '{"content":"hi"}')"
  [ "$code" = "404" ] && ok "向未知会话发消息返回 404" || no "未知会话发消息返回 ${code:-无响应}，期望 404"
fi

# ---------- 8. 前端契约 ----------
home_html="$(fetch "${BASE_URL}/")"
for needle in 'id="session-list"' 'id="chat-log"' 'id="new-session-btn"' 'class="composer"'; do
  if printf '%s' "$home_html" | grep -qF "$needle"; then
    ok "页面包含 ${needle}"
  else
    no "页面缺少 ${needle}"
  fi
done

printf '\n结果：%d 通过 / %d 失败 / %d 跳过\n' "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ] || exit 1
printf 'S7 验收通过。\n\n'
