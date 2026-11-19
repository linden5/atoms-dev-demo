#!/usr/bin/env bash
# S6 验收：安全响应头、预览隔离、空态/错误态，以及可选的 s1–s5 全量回归
# 用法：bash scripts/verify_s6.sh                        # 只跑 S6 检查
#       FULL=1 bash scripts/verify_s6.sh                 # 再串跑 verify_s1…s5（会新建演示任务）
#       TARGET=public bash scripts/verify_s6.sh          # 公网
#       BASE_URL=http://IP:8000 bash scripts/verify_s6.sh
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
SKIP=0

ok() { PASS=$((PASS + 1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
no() { FAIL=$((FAIL + 1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
skip() { SKIP=$((SKIP + 1)); printf '  \033[33mSKIP\033[0m  %s\n' "$1"; }
info() { printf '  \033[36m···\033[0m  %s\n' "$1"; }

fetch() { curl -fsS --max-time 8 "$1" 2>/dev/null; }
status_of() { curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$@" 2>/dev/null || true; }
headers_of() { curl -s -D - -o /dev/null --max-time 8 "$@" 2>/dev/null; }

json_field() {
  printf '%s' "$1" | grep -o "\"$2\":\"[^\"]*\"" | head -n 1 | cut -d'"' -f4
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

printf '\nS6 验收 · %s\n\n' "$BASE_URL"

# ---------- 1. 版本与健康 ----------
health="$(fetch "${BASE_URL}/health")"
if [ -z "$health" ]; then
  printf '  \033[31mFAIL\033[0m  /health 无响应，服务未启动？\n\nS6 验收中止。\n\n'
  exit 1
fi
ok "/health 可用"

step="${health#*\"step\":\"s}"
step="${step%%\"*}"
case "${step}" in
  '' | *[!0-9]*) no "步骤号无法解析" ;;
  *) [ "$step" -ge 6 ] && ok "当前步骤为 s${step}" || no "当前步骤是 s${step}，S6 尚未上线" ;;
esac

# ---------- 2. 主站安全响应头 ----------
site_headers="$(headers_of "${BASE_URL}/")"
if printf '%s' "$site_headers" | grep -qi 'content-security-policy:.*default-src'; then
  ok "主站返回 CSP"
else
  no "主站缺少 CSP 响应头"
fi
if printf '%s' "$site_headers" | grep -qi "script-src 'self'"; then
  ok "主站 CSP 只允许同源脚本"
else
  no "主站 CSP 未收紧 script-src"
fi
if printf '%s' "$site_headers" | grep -qi 'x-content-type-options: *nosniff'; then
  ok "带 X-Content-Type-Options: nosniff"
else
  no "缺少 X-Content-Type-Options"
fi
if printf '%s' "$site_headers" | grep -qi 'referrer-policy: *no-referrer'; then
  ok "带 Referrer-Policy: no-referrer"
else
  no "缺少 Referrer-Policy"
fi
if printf '%s' "$site_headers" | grep -qi 'x-frame-options: *deny'; then
  ok "主站禁止被 iframe 嵌套"
else
  no "主站缺少 X-Frame-Options: DENY"
fi
if printf '%s' "$site_headers" | grep -qi 'cache-control: *no-store'; then
  ok "主站 HTML 不缓存"
else
  no "主站 HTML 缺少 Cache-Control: no-store"
fi

# ---------- 3. 预览隔离 ----------
task_json="$(post_generate '{"prompt":"S6 验收：安全与空态"}')"
task_id="$(json_field "$task_json" task_id)"
if [ -z "$task_id" ]; then
  no "建任务失败（响应：${task_json:-空}）"
else
  started="$(date +%s)"
  while :; do
    detail="$(fetch "${BASE_URL}/api/tasks/${task_id}")"
    [ "$(json_field "$detail" status)" = "succeeded" ] && break
    [ $(($(date +%s) - started)) -ge 900 ] && break
    sleep 0.5
  done
  [ "$(json_field "$detail" status)" = "succeeded" ] && ok "验收任务生成成功" || no "验收任务未成功"

  preview_headers="$(headers_of "${BASE_URL}/api/tasks/${task_id}/preview")"
  if printf '%s' "$preview_headers" | grep -qi 'content-security-policy:.*unsafe-inline'; then
    ok "预览页使用独立的宽松 CSP（允许内联样式/脚本）"
  else
    no "预览页 CSP 不符合预期"
  fi
  if printf '%s' "$preview_headers" | grep -qi 'x-frame-options'; then
    no "预览页带了 X-Frame-Options，会导致 iframe 预览失败"
  else
    ok "预览页未被 X-Frame-Options 拦截"
  fi

  # 主站 iframe 必须带 sandbox 且不能有 allow-same-origin
  home_html="$(fetch "${BASE_URL}/")"
  if printf '%s' "$home_html" | grep -qF 'sandbox="allow-scripts"'; then
    ok "预览 iframe 使用 sandbox=\"allow-scripts\""
  else
    no "预览 iframe 缺少 sandbox"
  fi
  if printf '%s' "$home_html" | grep -qF 'allow-same-origin'; then
    no "iframe 的 sandbox 含 allow-same-origin（会破坏隔离）"
  else
    ok "iframe 的 sandbox 不含 allow-same-origin"
  fi
fi

# ---------- 4. 空态 / 错误态 / 加载态 ----------
home_html="$(fetch "${BASE_URL}/")"
for needle in 'id="empty-state"' 'id="error-banner"' 'id="preview-loading"' 'id="retry-btn"'; do
  if printf '%s' "$home_html" | grep -qF "$needle"; then
    ok "页面包含 ${needle}"
  else
    no "页面缺少 ${needle}"
  fi
done

# ---------- 5. 错误处理不泄露内部信息 ----------
expect_404() {
  local label="$1" code
  shift
  code="$(status_of "$@")"
  if [ "$code" = "404" ]; then ok "$label"; else no "$label（返回 ${code:-无响应}，期望 404）"; fi
}

expect_404 "未知任务详情返回 404" "${BASE_URL}/api/tasks/not-exist-task"
expect_404 "未知任务预览返回 404" "${BASE_URL}/api/tasks/not-exist-task/preview"
expect_404 "未知接口返回 404" "${BASE_URL}/api/__not_implemented__"
expect_404 "路径穿越被拒绝" --path-as-is "${BASE_URL}/api/tasks/${task_id:-not-exist}/files/../../app/main.py"

# 404 的响应体不应包含服务器路径
notfound_body="$(curl -sS --max-time 5 "${BASE_URL}/api/tasks/not-exist-task" 2>/dev/null)"
if printf '%s' "$notfound_body" | grep -qE 'Traceback|C:\\\\|/home/|site-packages'; then
  no "404 响应体泄露了服务端路径或堆栈"
else
  ok "404 响应体不泄露服务端路径"
fi

# ---------- 6. 接口文档仍可访问（CSP 豁免） ----------
docs_code="$(status_of "${BASE_URL}/api/docs")"
[ "$docs_code" = "200" ] && ok "/api/docs 可访问（CSP 豁免生效）" || no "/api/docs 返回 ${docs_code:-无响应}"

# ---------- 7. 可选：全量回归 ----------
if [ "${FULL:-0}" = "1" ]; then
  printf '\n  全量回归（verify_s1…s5）\n'
  for script in verify_s1.sh verify_s2.sh verify_s3.sh verify_s4.sh verify_s5.sh; do
    if [ ! -f "$ROOT_DIR/scripts/$script" ]; then
      skip "$script 不存在"
      continue
    fi
    if bash "$ROOT_DIR/scripts/$script" >"/tmp/${script}.log" 2>&1; then
      ok "$script 通过（$(grep '结果：' "/tmp/${script}.log" | tail -n 1 | tr -d '\r')）"
    else
      no "$script 失败，详见 /tmp/${script}.log"
    fi
  done
else
  skip "未设置 FULL=1，跳过 s1–s5 全量回归"
fi

printf '\n结果：%d 通过 / %d 失败 / %d 跳过\n' "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ] || exit 1
printf 'S6 验收通过。\n\n'
