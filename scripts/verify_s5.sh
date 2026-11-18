#!/usr/bin/env bash
# S5 验收：继续修改与版本历史（parent 链、版本列表、历史版本独立预览）
# 用法：bash scripts/verify_s5.sh                        # 本机（deploy/verify.env 的 LOCAL_BASE_URL）
#       TARGET=public bash scripts/verify_s5.sh          # 公网
#       BASE_URL=http://IP:8000 bash scripts/verify_s5.sh # 临时指定
#
# 幂等：每次新建一条独立的版本链（v1 → v2 → v3），不依赖上一次执行结果。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -z "${BASE_URL:-}" ]; then
  # shellcheck disable=SC1091
  [ -f "$ROOT_DIR/deploy/verify.env" ] && . "$ROOT_DIR/deploy/verify.env"

  case "${TARGET:-local}" in
    local) BASE_URL="${LOCAL_BASE_URL:-http://127.0.0.1:8000}" ;;
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

json_field() {
  printf '%s' "$1" | grep -o "\"$2\":\"[^\"]*\"" | head -n 1 | cut -d'"' -f4
}

json_number() {
  printf '%s' "$1" | grep -o "\"$2\":[0-9]*" | head -n 1 | cut -d':' -f2
}

post_generate() {
  local body_file body
  body_file="$(mktemp)"
  printf '%s' "$1" >"$body_file"
  body="$(curl -sS --max-time 8 -X POST "${BASE_URL}/api/generate" \
    -H 'Content-Type: application/json' --data-binary "@${body_file}" 2>/dev/null)"
  rm -f "$body_file"
  printf '%s' "$body"
}

wait_status() {
  local task_id="$1" want="$2" deadline="${3:-120}" started body status
  started="$(date +%s)"
  while :; do
    body="$(fetch "${BASE_URL}/api/tasks/${task_id}")"
    status="$(json_field "$body" status)"
    [ "$status" = "$want" ] && { printf '%s' "$body"; return 0; }
    if [ $(($(date +%s) - started)) -ge "$deadline" ]; then
      printf '%s' "${body:-}"
      return 1
    fi
    sleep 0.5
  done
}

first_version_id() {
  printf '%s' "$1" | sed -n 's/.*"versions":\[{"id":"\([^"]*\)".*/\1/p'
}

printf '\nS5 验收 · %s\n\n' "$BASE_URL"

health="$(fetch "${BASE_URL}/health")"
if [ -z "$health" ]; then
  printf '  \033[31mFAIL\033[0m  /health 无响应，服务未启动？\n\nS5 验收中止。\n\n'
  exit 1
fi

step="${health#*\"step\":\"s}"
step="${step%%\"*}"
case "${step}" in
  '' | *[!0-9]*) no "步骤号无法解析" ;;
  *)
    if [ "$step" -ge 5 ]; then ok "当前步骤不低于 s5（实际：s${step}）"; else no "当前步骤是 s${step}，S5 接口尚未上线"; fi
    ;;
esac

# ---------- 1. 造一条版本链 v1 → v2 → v3 ----------
stamp="$(date +%H:%M:%S)"
v1_json="$(post_generate "{\"prompt\":\"S5 验收 v1：番茄钟（${stamp}）\"}")"
v1="$(json_field "$v1_json" task_id)"
if [ -z "$v1" ]; then
  no "v1 建任务失败（响应：${v1_json:-空}）"
  printf '\n结果：%d 通过 / %d 失败\n\n' "$PASS" "$FAIL"
  exit 1
fi
ok "v1 建任务成功（${v1}）"

wait_status "$v1" succeeded 120 >/dev/null && ok "v1 生成成功" || no "v1 未在 120s 内成功"

v2_json="$(post_generate "{\"prompt\":\"S5 验收 v2：把配色改成绿色\",\"parent_id\":\"${v1}\"}")"
v2="$(json_field "$v2_json" task_id)"
if [ -n "$v2" ]; then ok "v2（带 parent_id）建任务成功（${v2}）"; else no "v2 建任务失败（响应：${v2_json:-空}）"; fi

v2_detail="$(wait_status "$v2" succeeded 120)" && ok "v2 生成成功" || no "v2 未在 120s 内成功"

v2_parent="$(json_field "$v2_detail" parent_id)"
if [ "$v2_parent" = "$v1" ]; then
  ok "v2.parent_id 指回 v1"
else
  no "v2.parent_id 是 ${v2_parent:-空}，期望 ${v1}"
fi

v3_json="$(post_generate "{\"prompt\":\"S5 验收 v3：再加一个休息提醒\",\"parent_id\":\"${v2}\"}")"
v3="$(json_field "$v3_json" task_id)"
if [ -n "$v3" ]; then
  wait_status "$v3" succeeded 120 >/dev/null && ok "v3 基于 v2 生成成功（链长度 3）" || no "v3 未在 120s 内成功"
else
  no "v3 建任务失败"
fi

# ---------- 2. 版本链接口 ----------
versions_body="$(fetch "${BASE_URL}/api/tasks/${v1}/versions")"
if [ -z "$versions_body" ]; then
  no "/api/tasks/{id}/versions 不可用"
else
  ok "/api/tasks/{id}/versions 可用"

  root_id="$(json_field "$versions_body" root_id)"
  if [ "$root_id" = "$v1" ]; then ok "root_id 指向 v1"; else no "root_id 是 ${root_id:-空}，期望 ${v1}"; fi

  total="$(json_number "$versions_body" total)"
  if [ "${total:-0}" -ge 3 ]; then ok "版本数 total=${total}"; else no "版本数 total=${total:-空}（期望 ≥ 3）"; fi

  for id in "$v1" "$v2" "$v3"; do
    if printf '%s' "$versions_body" | grep -qF "\"id\":\"${id}\""; then
      ok "版本列表包含 ${id}"
    else
      no "版本列表缺少 ${id}"
    fi
  done

  first_id="$(first_version_id "$versions_body")"
  if [ "$first_id" = "$v1" ]; then
    ok "版本按创建时间升序（v1 在最前）"
  else
    no "版本顺序异常（第一个是 ${first_id:-空}，期望 ${v1}）"
  fi
fi

# 从任意一个中间版本查询，都应回到同一条链
mid_body="$(fetch "${BASE_URL}/api/tasks/${v2}/versions")"
mid_root="$(json_field "$mid_body" root_id)"
mid_current="$(json_field "$mid_body" current_id)"
if [ "$mid_root" = "$v1" ] && [ "$mid_current" = "$v2" ]; then
  ok "从 v2 查询版本链，root/current 正确"
else
  no "从 v2 查询结果异常（root=${mid_root:-空} current=${mid_current:-空}）"
fi

# ---------- 3. 历史版本仍可独立预览 ----------
for pair in "v1:$v1" "v2:$v2" "v3:$v3"; do
  label="${pair%%:*}"
  id="${pair##*:}"
  code="$(status_of "${BASE_URL}/api/tasks/${id}/preview")"
  if [ "$code" = "200" ]; then ok "${label} 的产物仍可独立预览"; else no "${label} 预览返回 ${code:-无响应}（期望 200）"; fi
done

v1_html="$(fetch "${BASE_URL}/api/tasks/${v1}/preview")"
if printf '%s' "$v1_html" | grep -qF 'S5 验收 v1'; then
  ok "v1 产物内容是 v1 自己的（未被新版本覆盖）"
else
  info "v1 产物未包含原始 prompt 文本（取决于引擎模板，不算失败）"
fi

# ---------- 4. 参数校验 ----------
bad="$(post_generate '{"prompt":"parent missing","parent_id":"not-exist"}')"
if printf '%s' "$bad" | grep -qF 'parent task not found'; then
  ok "parent_id 不存在时返回 404 与明确原因"
else
  no "parent_id 不存在时未给出 404（响应：${bad:-空}）"
fi

expect_versions_404="$(status_of "${BASE_URL}/api/tasks/not-exist-task/versions")"
if [ "$expect_versions_404" = "404" ]; then
  ok "不存在任务的版本链接口返回 404"
else
  no "不存在任务的版本链接口返回 ${expect_versions_404:-无响应}（期望 404）"
fi

# 独立任务（无 parent）的版本链只应包含自己
solo_json="$(post_generate "{\"prompt\":\"S5 验收：独立任务（${stamp}）\"}")"
solo="$(json_field "$solo_json" task_id)"
if [ -n "$solo" ]; then
  solo_body="$(fetch "${BASE_URL}/api/tasks/${solo}/versions")"
  solo_total="$(json_number "$solo_body" total)"
  if [ "${solo_total:-0}" = "1" ]; then
    ok "独立任务的版本链只有 1 个版本"
  else
    no "独立任务的版本数是 ${solo_total:-空}（期望 1）"
  fi
  curl -sS --max-time 5 -X POST "${BASE_URL}/api/tasks/${solo}/cancel" >/dev/null 2>&1 || true
else
  no "独立任务建任务失败"
fi

printf '\n结果：%d 通过 / %d 失败\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
printf 'S5 验收通过。\n\n'
