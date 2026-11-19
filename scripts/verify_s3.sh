#!/usr/bin/env bash
# S3 验收：任务 CRUD + SQLite + MOCK 引擎 + 产物预览是否真的端到端可用
# 用法：bash scripts/verify_s3.sh                        # 本机（deploy/verify.env 的 LOCAL_BASE_URL）
#       TARGET=public bash scripts/verify_s3.sh          # 公网（deploy/verify.env 的 PUBLIC_BASE_URL）
#       BASE_URL=http://IP:8000 bash scripts/verify_s3.sh # 临时指定，优先级最高
#
# 幂等：每次都新建两条验收任务（一条跑到成功、一条立即取消），不依赖上一次执行结果。
# 退出码 0 表示全部通过；失败项会逐条打印。
set -uo pipefail

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
info() { printf '  \033[36m···\033[0m  %s\n' "$1"; }

fetch() { curl -fsS --max-time 8 "$1" 2>/dev/null; }

status_of() { curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$@" 2>/dev/null || true; }

expect_status() {
  local label="$1" want="$2" code
  shift 2
  code="$(status_of "$@")"
  if [ "$code" = "$want" ]; then
    ok "$label"
  else
    no "$label（返回 ${code:-无响应}，期望 $want）"
  fi
}

expect_contains() {
  local label="$1" url="$2" needle="$3" body
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

# JSON 取值：不引入 jq，只处理本脚本用到的扁平字段
json_field() {
  printf '%s' "$1" | grep -o "\"$2\":\"[^\"]*\"" | head -n 1 | cut -d'"' -f4
}

json_number() {
  printf '%s' "$1" | grep -o "\"$2\":[0-9.]*" | head -n 1 | cut -d':' -f2
}

step_number() {
  local raw="${1#s}"
  case "$raw" in
    '' | *[!0-9]*) printf '0' ;;
    *) printf '%s' "$raw" ;;
  esac
}

# 轮询任务直到终态（MOCK 约 2.5s，真实引擎可能更久，上限 60s）
wait_terminal() {
  local task_id="$1" deadline="${2:-60}" started body status
  started="$(date +%s)"
  while :; do
    body="$(fetch "${BASE_URL}/api/tasks/${task_id}")"
    status="$(json_field "$body" status)"
    case "$status" in
      succeeded | failed | cancelled) printf '%s' "$status"; return 0 ;;
    esac
    if [ $(($(date +%s) - started)) -ge "$deadline" ]; then
      printf '%s' "${status:-timeout}"
      return 1
    fi
    sleep 0.5
  done
}

printf '\nS3 验收 · %s\n\n' "$BASE_URL"

# ---------- 1. 服务身份 ----------
health="$(fetch "${BASE_URL}/health")"
if [ -z "$health" ]; then
  printf '  \033[31mFAIL\033[0m  /health 无响应，服务未启动？\n\nS3 验收中止。\n\n'
  exit 1
fi
ok "/health 可用"

step="$(json_field "$health" step)"
if [ "$(step_number "$step")" -ge 3 ]; then
  ok "当前步骤不低于 s3（实际：${step}）"
else
  no "当前步骤是 ${step:-未知}，S3 接口尚未上线"
fi

engine="$(json_field "$health" engine)"
if [ -n "$engine" ] && [ "$engine" != "unavailable" ]; then
  ok "引擎已装配（${engine}）"
else
  no "引擎不可用（MOCK_ENGINE=0 但真实引擎缺失？）"
fi

# 请求体写到临时文件再发送：Windows 下命令行参数会被转成本地代码页，中文会变成非 UTF-8 字节
post_generate() {
  local body_file body
  body_file="$(mktemp)"
  printf '%s' "$1" >"$body_file"
  body="$(curl -sS --max-time 8 -X POST "${BASE_URL}/api/generate" \
    -H 'Content-Type: application/json' \
    --data-binary "@${body_file}" 2>/dev/null)"
  rm -f "$body_file"
  printf '%s' "$body"
}

# ---------- 2. 建任务：必须立刻返回 ----------
created="$(post_generate "{\"prompt\":\"验收任务：做一个番茄钟（$(date +%H:%M:%S)）\"}")"
task_id="$(json_field "$created" task_id)"

if [ -n "$task_id" ]; then
  ok "POST /api/generate 返回 task_id（${task_id}）"
else
  no "POST /api/generate 未返回 task_id（响应：${created:-空}）"
  printf '\n结果：%d 通过 / %d 失败\n\n' "$PASS" "$FAIL"
  exit 1
fi

accept_ms="$(json_number "$created" accept_ms)"
if [ -n "$accept_ms" ] && awk "BEGIN{exit !($accept_ms < 200)}"; then
  ok "任务接受耗时 ${accept_ms}ms < 200ms"
else
  no "任务接受耗时 ${accept_ms:-未知}ms，未达到 < 200ms 目标"
fi

# ---------- 3. 状态机推进到 succeeded ----------
final="$(wait_terminal "$task_id" 60)"
if [ "$final" = "succeeded" ]; then
  ok "任务状态推进到 succeeded"
else
  no "任务未成功（最终状态：${final}）"
fi

detail="$(fetch "${BASE_URL}/api/tasks/${task_id}")"

if printf '%s' "$detail" | grep -qF '"stage":"checking"'; then
  ok "最终 stage 为 checking"
else
  no "最终 stage 不是 checking"
fi

for stage in planning coding checking; do
  if printf '%s' "$detail" | grep -qF "\"message\":\"${stage}\""; then
    ok "事件流记录了阶段 ${stage}"
  else
    no "事件流缺少阶段 ${stage}"
  fi
done

if printf '%s' "$detail" | grep -qF '"kind":"html"'; then
  ok "产物列表含 html（artifacts 已落库）"
else
  no "产物列表没有 html，scan_artifacts 可能失败"
fi

if printf '%s' "$detail" | grep -qF '"entry":"index.html"'; then
  ok "入口文件为 index.html"
else
  info "入口文件不是 index.html（允许，取决于引擎产物结构）"
fi

for artifact in index.html styles.css app.js; do
  expect_status "产物 ${artifact} 可下载" "200" "${BASE_URL}/api/tasks/${task_id}/files/${artifact}"
done

# ---------- 4. 预览：HTML + CSP ----------
preview_status="$(status_of "${BASE_URL}/api/tasks/${task_id}/preview")"
if [ "$preview_status" = "200" ]; then
  ok "/preview 返回 200"
else
  no "/preview 返回 ${preview_status:-无响应}（期望 200）"
fi

preview_headers="$(curl -s -D - -o /dev/null --max-time 8 "${BASE_URL}/api/tasks/${task_id}/preview" 2>/dev/null || true)"
if printf '%s' "$preview_headers" | grep -qi 'content-type: text/html'; then
  ok "/preview 的 Content-Type 是 text/html"
else
  no "/preview 的 Content-Type 不是 text/html"
fi
if printf '%s' "$preview_headers" | grep -qi 'content-security-policy:'; then
  ok "/preview 带 CSP 响应头"
else
  no "/preview 缺 CSP 响应头"
fi

expect_contains "预览内容是可解析的 HTML" "${BASE_URL}/api/tasks/${task_id}/preview" "<!doctype html"

# ---------- 5. 安全：路径穿越与不存在的任务 ----------
expect_status "路径穿越 ../ 被拒绝" "404" --path-as-is "${BASE_URL}/api/tasks/${task_id}/files/../../app/main.py"
expect_status "编码后的路径穿越被拒绝" "404" "${BASE_URL}/api/tasks/${task_id}/files/%2e%2e%2f%2e%2e%2fapp%2fmain.py"
expect_status "不存在任务的详情返回 404" "404" "${BASE_URL}/api/tasks/not-exist-task"
expect_status "不存在任务的预览返回 404" "404" "${BASE_URL}/api/tasks/not-exist-task/preview"

# ---------- 6. 列表与持久化 ----------
list="$(fetch "${BASE_URL}/api/tasks?limit=50")"
if printf '%s' "$list" | grep -qF "\"id\":\"${task_id}\""; then
  ok "任务出现在列表中"
else
  no "任务未出现在列表中"
fi
total="$(json_number "$list" total)"
if [ -n "$total" ] && [ "$total" -ge 1 ]; then
  ok "列表 total=${total}"
else
  no "列表 total 异常（${total:-空}）"
fi

# 历史任务（本次脚本之前就存在的）必须仍可读取，证明数据落库而非内存态
older_id="$(printf '%s' "$list" | grep -o '"id":"[^"]*"' | cut -d'"' -f4 | grep -v "^${task_id}$" | head -n 1)"
if [ -n "$older_id" ]; then
  body_older="$(fetch "${BASE_URL}/api/tasks/${older_id}")"
  if [ -n "$body_older" ]; then
    ok "更早的任务仍可读取（${older_id}），数据已持久化"
  else
    no "更早的任务读取失败（${older_id}）"
  fi
else
  info "没有更早的任务，跳过持久化对比（重跑一次本脚本即可覆盖此项）"
fi

# ---------- 7. 取消 ----------
queued="$(post_generate '{"prompt":"验收任务：这条会被立刻取消"}')"
cancel_id="$(json_field "$queued" task_id)"
if [ -n "$cancel_id" ]; then
  cancelled="$(curl -sS --max-time 8 -X POST "${BASE_URL}/api/tasks/${cancel_id}/cancel" 2>/dev/null)"
  if printf '%s' "$cancelled" | grep -qF '"status":"cancelled"'; then
    ok "取消接口把任务置为 cancelled"
  else
    no "取消接口未返回 cancelled（响应：${cancelled:-空}）"
  fi
  sleep 0.5
  after="$(fetch "${BASE_URL}/api/tasks/${cancel_id}")"
  if printf '%s' "$after" | grep -qF '"status":"cancelled"'; then
    ok "取消后的状态稳定，未被引擎改回 running/failed"
  else
    no "取消后状态被改写（详情：${after:-空}）"
  fi
else
  no "取消用例建任务失败"
fi

printf '\n结果：%d 通过 / %d 失败\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
printf 'S3 验收通过。\n\n'
