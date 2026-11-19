#!/usr/bin/env bash
# S4 验收：真实引擎接入（可切换）+ 引擎层工具 + 超时失败路径
# 用法：bash scripts/verify_s4.sh                        # 本机（deploy/verify.env 的 LOCAL_BASE_URL）
#       TARGET=public bash scripts/verify_s4.sh          # 公网（只跑服务层检查，引擎层检查仍在本机执行）
#       BASE_URL=http://IP:8000 bash scripts/verify_s4.sh # 临时指定
#       SKIP_TIMEOUT=1 bash scripts/verify_s4.sh         # 跳过超时路径（会临时起一个隔离服务）
#
# 幂等：每次新建验收任务；临时服务与临时目录用完即删，不污染 data/ 与 workspace/。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

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

expect_status() {
  local label="$1" want="$2" code
  shift 2
  code="$(status_of "$@")"
  if [ "$code" = "$want" ]; then ok "$label"; else no "$label（返回 ${code:-无响应}，期望 $want）"; fi
}

json_field() {
  printf '%s' "$1" | grep -o "\"$2\":\"[^\"]*\"" | head -n 1 | cut -d'"' -f4
}

# 选一个可用的 Python：优先项目 venv，其次系统解释器
PYTHON_BIN=""
for candidate in ".venv/bin/python" ".venv/Scripts/python.exe"; do
  if [ -x "$candidate" ]; then PYTHON_BIN="$candidate"; break; fi
done
if [ -z "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi
# Windows 上 Python 默认按本地代码页输出，中文提示会让 grep 匹配不到；统一成 UTF-8
export PYTHONIOENCODING=utf-8

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
  local base="$1" task_id="$2" want="$3" deadline="${4:-30}" started body status
  started="$(date +%s)"
  while :; do
    body="$(curl -fsS --max-time 5 "${base}/api/tasks/${task_id}" 2>/dev/null)"
    status="$(json_field "$body" status)"
    [ "$status" = "$want" ] && { printf '%s' "$body"; return 0; }
    if [ $(($(date +%s) - started)) -ge "$deadline" ]; then
      printf '%s' "${body:-}"
      return 1
    fi
    sleep 0.5
  done
}

printf '\nS4 验收 · %s\n\n' "$BASE_URL"

# ---------- 1. 服务层：引擎状态可观测 ----------
health="$(fetch "${BASE_URL}/health")"
if [ -z "$health" ]; then
  printf '  \033[31mFAIL\033[0m  /health 无响应，服务未启动？\n\nS4 验收中止。\n\n'
  exit 1
fi
ok "/health 可用"

engine="$(json_field "$health" engine)"
case "$engine" in
  mock | metagpt) ok "引擎已装配（${engine}）" ;;
  unavailable) no "引擎不可用：MOCK_ENGINE=0 但没有可导入的 MetaGPT" ;;
  *) no "引擎字段异常：${engine:-空}" ;;
esac

mode="$(json_field "$health" engine_mode)"
case "$mode" in
  auto | mock | metagpt) ok "engine_mode=${mode}" ;;
  *) no "缺少 engine_mode 字段（实际：${mode:-空}）" ;;
esac

# 版本号按 major.minor 比较：后续步骤会继续涨版本，写死 0.4 会让回归变成噪音
app_version="$(json_field "$health" version)"
app_major="$(printf '%s' "$app_version" | cut -d. -f1)"
app_minor="$(printf '%s' "$app_version" | cut -d. -f2)"
case "${app_major}${app_minor}" in
  '' | *[!0-9]*) no "版本号无法解析（${app_version:-空}）" ;;
  *)
    if [ "$app_major" -ge 1 ] || [ "$app_minor" -ge 4 ]; then
      ok "版本号不低于 0.4（实际：${app_version}）"
    else
      no "版本号是 ${app_version}，S4 的引擎改动尚未上线"
    fi
    ;;
esac

# ---------- 2. 回归：S3 主链路在 S4 仍然可用 ----------
created="$(post_generate '{"prompt":"S4 验收：回归 S3 主链路"}')"
task_id="$(json_field "$created" task_id)"
if [ -n "$task_id" ]; then
  ok "建任务成功（${task_id}）"
else
  no "建任务失败（响应：${created:-空}）"
fi

if [ -n "$task_id" ]; then
  # 真实引擎单次 5–10 分钟，给足 900s；MOCK 约 2.5s
  detail="$(wait_status "$BASE_URL" "$task_id" succeeded 900)" && ok "任务推进到 succeeded" \
    || no "任务未成功（$(json_field "$detail" status)，error=$(json_field "$detail" error)）"
  expect_status "预览可用" "200" "${BASE_URL}/api/tasks/${task_id}/preview"
  expect_status "路径穿越仍被拒绝" "404" --path-as-is "${BASE_URL}/api/tasks/${task_id}/files/../../app/main.py"
fi

# ---------- 3. 引擎层：命令行单次生成 ----------
if [ -z "$PYTHON_BIN" ]; then
  skip "未找到 Python 解释器，跳过引擎层检查"
else
  TMP_DIR="$(mktemp -d)"
  OUT_DIR="$TMP_DIR/out"

  mock_out="$("$PYTHON_BIN" scripts/gen_once.py "S4 验收：命令行 mock 生成" --engine mock --out "$OUT_DIR" 2>&1)"
  if [ $? -eq 0 ] && [ -f "$OUT_DIR/index.html" ]; then
    ok "gen_once.py --engine mock 产出 index.html"
  else
    no "gen_once.py --engine mock 失败（${mock_out##*$'\n'}）"
  fi

  fail_out="$("$PYTHON_BIN" scripts/gen_once.py "S4 验收：故障注入" --fail --out "$TMP_DIR/fail" 2>&1)"
  fail_code=$?
  if [ "$fail_code" -ne 0 ] && printf '%s' "$fail_out" | grep -q '失败'; then
    ok "gen_once.py --fail 返回非 0 并给出失败原因"
  else
    no "gen_once.py --fail 没有按预期失败（退出码 ${fail_code}）"
  fi

  if "$PYTHON_BIN" -c "import metagpt" >/dev/null 2>&1; then
    info "当前环境已安装 MetaGPT，跳过\"不可用时明确报错\"的检查"
  else
    real_out="$("$PYTHON_BIN" scripts/gen_once.py "S4 验收：真实引擎不可用" --engine metagpt --out "$TMP_DIR/real" 2>&1)"
    real_code=$?
    if [ "$real_code" -ne 0 ] && printf '%s' "$real_out" | grep -q 'MetaGPT 不可用'; then
      ok "未装 MetaGPT 时强制 metagpt 会明确报错，不静默降级"
    else
      no "强制 metagpt 时没有给出明确报错（退出码 ${real_code}）"
    fi
  fi

  seed_out="$("$PYTHON_BIN" scripts/seed_demo.py --dry-run --count 2 2>&1)"
  if [ $? -eq 0 ] && printf '%s' "$seed_out" | grep -q '\[plan\]'; then
    ok "seed_demo.py 可运行（dry-run 打印预置计划）"
  else
    no "seed_demo.py 运行失败（${seed_out##*$'\n'}）"
  fi

  rm -rf "$TMP_DIR"
fi

# ---------- 4. 超时路径：隔离实例上跑 TASK_TIMEOUT=1 ----------
if [ "${SKIP_TIMEOUT:-0}" = "1" ]; then
  skip "已通过 SKIP_TIMEOUT=1 跳过超时路径检查"
elif [ -z "$PYTHON_BIN" ]; then
  skip "未找到 Python 解释器，跳过超时路径检查"
else
  PORT="${TIMEOUT_PORT:-8011}"
  ISO_DIR="$(mktemp -d)"
  ISO_LOG="$ISO_DIR/server.log"
  DATA_DIR="$ISO_DIR/data" WORKSPACE_DIR="$ISO_DIR/ws" MOCK_ENGINE=1 TASK_TIMEOUT=1 \
    "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --workers 1 \
    >"$ISO_LOG" 2>&1 &
  ISO_PID=$!

  cleanup_iso() { kill "$ISO_PID" 2>/dev/null; wait "$ISO_PID" 2>/dev/null; rm -rf "$ISO_DIR"; }
  trap cleanup_iso EXIT

  ISO_URL="http://127.0.0.1:${PORT}"
  ready=0
  for _ in $(seq 1 40); do
    if curl -fsS --max-time 2 "${ISO_URL}/health" >/dev/null 2>&1; then ready=1; break; fi
    sleep 0.5
  done

  if [ "$ready" -ne 1 ]; then
    no "隔离实例未在 20s 内就绪（日志：${ISO_LOG}）"
  else
    ok "隔离实例已启动（TASK_TIMEOUT=1，端口 ${PORT}）"
    iso_created="$(curl -sS --max-time 5 -X POST "${ISO_URL}/api/generate" \
      -H 'Content-Type: application/json' -d '{"prompt":"timeout path"}' 2>/dev/null)"
    iso_task="$(json_field "$iso_created" task_id)"
    if [ -z "$iso_task" ]; then
      no "隔离实例建任务失败（响应：${iso_created:-空}）"
    else
      if iso_detail="$(wait_status "$ISO_URL" "$iso_task" failed 30)"; then
        ok "超过 TASK_TIMEOUT 的任务被置为 failed"
      else
        no "超时任务未进入 failed（当前 $(json_field "$iso_detail" status)）"
      fi
      if printf '%s' "$iso_detail" | grep -q 'timeout'; then
        ok "失败原因里带 timeout 说明"
      else
        no "失败原因缺少 timeout（error=$(json_field "$iso_detail" error)）"
      fi
    fi
  fi

  cleanup_iso
  trap - EXIT
fi

printf '\n结果：%d 通过 / %d 失败 / %d 跳过\n' "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ] || exit 1
printf 'S4 验收通过。\n\n'
