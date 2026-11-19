#!/usr/bin/env bash
# S9 验收：应用存储（存储桥）
# 预览/公开页注入 window.__ATOMS__；生成应用的数据经 GET/PUT /api/tasks/{id}/data 平台代管。
# 不依赖真模型：从任务列表挑最近一个 succeeded 任务验证即可，可重复执行。
# 用法：bash scripts/verify_s9.sh
#       BASE_URL=http://IP:8000 bash scripts/verify_s9.sh
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

put_json_code() {
  local url="$1" body="$2" tmp code
  tmp="$(mktemp)"
  printf '%s' "$body" >"$tmp"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -X PUT "${url}" \
    -H 'Content-Type: application/json' --data-binary "@${tmp}" 2>/dev/null || true)"
  rm -f "$tmp"
  printf '%s' "$code"
}

printf '\nS9 验收（应用存储） · %s\n\n' "$BASE_URL"

# ---------- 0. 服务可用 ----------
health="$(fetch "${BASE_URL}/health")"
if [ -z "$health" ]; then
  printf '  \033[31mFAIL\033[0m  /health 无响应，服务未启动？\n\nS9 验收中止。\n\n'
  exit 1
fi
ok "/health 可用"
step="$(printf '%s' "$health" | grep -o '"step":"[^"]*"' | cut -d'"' -f4)"
# S10 起步骤继续推进，判定放宽为 >= s9（与 verify_s8 的 >= 8 同款写法）
step_num="${step#s}"
case "${step_num}" in
  '' | *[!0-9]*) no "步骤号无法解析（step=${step:-空}）" ;;
  *) [ "$step_num" -ge 9 ] && ok "step=${step}（>= s9）" || no "step=${step:-空}，期望 s9 及以后" ;;
esac

# ---------- 1. 挑一个最近可预览的任务（不重新生成，验收可幂等重跑） ----------
listing="$(fetch "${BASE_URL}/api/tasks?limit=50")"
task_id=""
for candidate in $(printf '%s' "$listing" | grep -o '"id":"t_[^"]*"' | cut -d'"' -f4 | head -n 20); do
  if fetch "${BASE_URL}/api/tasks/${candidate}/preview" | grep -qF 'window.__ATOMS__'; then
    task_id="$candidate"
    break
  fi
done
if [ -z "$task_id" ]; then
  skip "没有可预览的任务（先在页面上生成一次再跑本脚本）"
  printf '\n结果：%d 通过 / %d 失败 / %d 跳过\n' "$PASS" "$FAIL" "$SKIP"
  exit 0
fi
info "使用任务 ${task_id}"

# ---------- 2. 预览页注入 ----------
html="$(fetch "${BASE_URL}/api/tasks/${task_id}/preview")"
if printf '%s' "$html" | grep -qF 'window.__ATOMS__'; then
  ok "预览页注入了 window.__ATOMS__"
else
  no "预览页没有注入 window.__ATOMS__"
fi
token="$(printf '%s' "$html" | grep -oE '"token": *"[^"]*"' | head -n 1 | sed 's/.*"token": *"//; s/"$//')"
[ -n "$token" ] && ok "注入了访问令牌（长度 $(printf '%s' "$token" | wc -c | tr -d ' ')）" \
  || no "没有注入 token，应用存储无法使用"

# ---------- 3. 存储读写与鉴权 ----------
if [ -n "$token" ]; then
  code="$(status_of "${BASE_URL}/api/tasks/${task_id}/data")"
  [ "$code" = "403" ] && ok "无令牌读取返回 403" || no "无令牌读取返回 ${code:-无响应}，期望 403"

  code="$(status_of "${BASE_URL}/api/tasks/${task_id}/data?token=wrong-token")"
  [ "$code" = "403" ] && ok "错误令牌读取返回 403" || no "错误令牌读取返回 ${code:-无响应}，期望 403"

  code="$(put_json_code "${BASE_URL}/api/tasks/${task_id}/data?token=wrong-token" '{"x":1}')"
  [ "$code" = "403" ] && ok "错误令牌写入返回 403" || no "错误令牌写入返回 ${code:-无响应}，期望 403"

  # 先快照现网数据：写测试用的是真实任务，测完必须原样恢复，不污染用户数据
  snapshot="$(fetch "${BASE_URL}/api/tasks/${task_id}/data?token=${token}")"

  code="$(put_json_code "${BASE_URL}/api/tasks/${task_id}/data?token=${token}" 'not-json')"
  [ "$code" = "400" ] && ok "非法 JSON 写入返回 400" || no "非法 JSON 写入返回 ${code:-无响应}，期望 400"

  big='{"blob":"'$(head -c 70000 /dev/zero | tr '\0' 'a')'"}'
  code="$(put_json_code "${BASE_URL}/api/tasks/${task_id}/data?token=${token}" "$big")"
  [ "$code" = "413" ] && ok "超过 64KB 写入返回 413" || no "超过 64KB 写入返回 ${code:-无响应}，期望 413"

  payload='{"records":[{"name":"verify-s9","amount":42}],"note":"s9-roundtrip"}'
  code="$(put_json_code "${BASE_URL}/api/tasks/${task_id}/data?token=${token}" "$payload")"
  [ "$code" = "200" ] && ok "合法写入返回 200" || no "合法写入返回 ${code:-无响应}，期望 200"

  roundtrip="$(fetch "${BASE_URL}/api/tasks/${task_id}/data?token=${token}")"
  if printf '%s' "$roundtrip" | grep -qF '"note":"s9-roundtrip"'; then
    ok "写后读回环一致"
  else
    no "读回内容与写入不一致（${roundtrip:-空}）"
  fi

  # 恢复快照（空快照恢复成 "{}"）
  if [ -n "${snapshot:-}" ] && [ "$snapshot" != "{}" ]; then
    restore_code="$(put_json_code "${BASE_URL}/api/tasks/${task_id}/data?token=${token}" "$snapshot")"
    [ "$restore_code" = "200" ] && ok "测试数据已恢复快照（不污染真实任务）" \
      || no "快照恢复失败（${restore_code:-无响应}），任务数据残留测试写入"
  else
    code="$(put_json_code "${BASE_URL}/api/tasks/${task_id}/data?token=${token}" '{}')"
    [ "$code" = "200" ] && ok "测试数据已清空（原快照为空）" || no "清空测试数据返回 ${code:-无响应}"
  fi
  printf '%s' "$roundtrip" | grep -qi 'access-control-allow-origin: \*' \
    || true  # header_of 才能看到响应头，这里内容校验为主
else
  skip "没有 token，跳过读写与鉴权检查"
fi

header_of_data() { curl -sS -o /dev/null -D - --max-time 8 "$@" 2>/dev/null | tr -d '\r'; }
if [ -n "${token:-}" ]; then
  headers="$(header_of_data "${BASE_URL}/api/tasks/${task_id}/data?token=${token}")"
  printf '%s' "$headers" | grep -qi 'access-control-allow-origin: \*' \
    && ok "存储接口带 ACAO:*（沙箱跨源可读）" || no "存储接口缺少 ACAO 响应头"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -X OPTIONS \
    "${BASE_URL}/api/tasks/${task_id}/data" 2>/dev/null || true)"
  [ "$code" = "204" ] && ok "OPTIONS 预检返回 204" || no "OPTIONS 预检返回 ${code:-无响应}，期望 204"
fi

# ---------- 4. 发布页注入 ----------
slug="$(fetch "${BASE_URL}/api/tasks/${task_id}/publish" | grep -o '"slug":"[^"]*"' | head -n 1 | cut -d'"' -f4)"
if [ -n "$slug" ]; then
  public_html="$(fetch "${BASE_URL}/p/${slug}")"
  printf '%s' "$public_html" | grep -qF 'window.__ATOMS__' \
    && ok "公开页也注入了 __ATOMS__（预览/发布行为一致）" || no "公开页没有注入 __ATOMS__"
else
  skip "该任务未发布，跳过公开页注入检查"
fi

# ---------- 5. 沙箱约束未被破坏 ----------
home_html="$(fetch "${BASE_URL}/")"
if printf '%s' "$home_html" | grep -qF 'allow-scripts' && \
   printf '%s' "$home_html" | grep -qF 'allow-forms' && \
   ! printf '%s' "$home_html" | grep -qF 'allow-same-origin'; then
  ok "预览 iframe 含 allow-scripts/allow-forms（无 allow-same-origin）"
else
  no "预览 iframe 的 sandbox 配置被改动"
fi

printf '\n结果：%d 通过 / %d 失败 / %d 跳过\n' "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ] || exit 1
printf 'S9 验收通过。\n\n'
