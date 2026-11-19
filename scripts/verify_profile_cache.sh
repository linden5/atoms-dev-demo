#!/usr/bin/env bash
# 能力档 + LLM 缓存的本地验收（不需要真实 Key、不需要启服务、不需要 MetaGPT）
#
# 验证三件事：
#   1. 档位文件能被装载，显式环境变量优先，非法档位名/缺文件会明确报错；
#   2. 同一个 prompt 第二次生成命中缓存（把假端点关掉也能成功，证明真的没发请求）；
#   3. 换模型 / 关缓存时不命中；switch_model.sh 的校验能挡住坏档位。
#
# 用法：bash scripts/verify_profile_cache.sh
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PY="$ROOT_DIR/.venv/bin/python"
elif [ -x "$ROOT_DIR/.venv/Scripts/python.exe" ]; then
  PY="$ROOT_DIR/.venv/Scripts/python.exe"
else
  PY="$(command -v python3 || command -v python || true)"
fi
[ -n "$PY" ] || { printf '找不到 python\n' >&2; exit 1; }

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
no() { FAIL=$((FAIL + 1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
info() { printf '  \033[36m···\033[0m  %s\n' "$1"; }

TMP="$(mktemp -d)"
PROFILE_DIR="$TMP/profiles"
WORKSPACE_DIR="$TMP/ws"
PROFILE="e2e"
PORT=$((8100 + RANDOM % 300))
SERVER_PID=""

cleanup() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  rm -rf "$TMP"
}
trap cleanup EXIT

start_fake_llm() {
  "$PY" scripts/fake_llm.py --port "$PORT" --scenario success >"$TMP/fake_llm.log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 40); do
    if curl -s -o /dev/null --max-time 1 "http://127.0.0.1:${PORT}/"; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

stop_fake_llm() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  SERVER_PID=""
  sleep 0.3
}

meta_field() {
  grep -o "\"$2\": *[0-9]*" "$1" | head -n 1 | grep -o '[0-9]*'
}

run_gen() {
  local out="$1"
  env \
    LLM_PROFILE="$PROFILE" \
    LLM_PROFILE_DIR="$PROFILE_DIR" \
    WORKSPACE_DIR="$WORKSPACE_DIR" \
    ENGINE_MODE=llm \
    TASK_TIMEOUT=60 \
    "$PY" scripts/gen_once.py "做一个待办清单，可以增删条目" --engine llm --timeout 60 --out "$out" \
    >>"$TMP/gen.log" 2>&1
}

printf '\n能力档 + LLM 缓存验收（本地，假端点）\n\n'

# ---------- 0. 准备档位文件 ----------
mkdir -p "$PROFILE_DIR"
cat >"$PROFILE_DIR/$PROFILE.env" <<EOF
ENGINE_MODE=llm
LLM_BASE_URL=http://127.0.0.1:${PORT}/v1
LLM_MODEL=fake-model
LLM_API_KEY=dummy-key-not-a-real-secret
LLM_MAX_TOKENS=2000
EOF
chmod 600 "$PROFILE_DIR/$PROFILE.env"
ok "档位文件已生成（权限 600）"

# ---------- 1. 档位装载 ----------
out="$(env LLM_PROFILE="$PROFILE" LLM_PROFILE_DIR="$PROFILE_DIR" \
  "$PY" -c 'from app.config import settings; print(settings.llm_profile, settings.llm_model, settings.engine_mode)')"
case "$out" in
  "$PROFILE fake-model llm") ok "档位装载：$out" ;;
  *) no "档位装载结果异常：$out" ;;
esac

out="$(env LLM_PROFILE="$PROFILE" LLM_PROFILE_DIR="$PROFILE_DIR" LLM_MODEL=override-model \
  "$PY" -c 'from app.config import settings; print(settings.llm_model)')"
[ "$out" = "override-model" ] && ok "显式环境变量优先于档位文件" || no "环境变量没有优先：$out"

if env LLM_PROFILE="../etc/passwd" LLM_PROFILE_DIR="$PROFILE_DIR" \
  "$PY" -c 'from app.config import settings' >"$TMP/badname.log" 2>&1; then
  no "非法档位名没有被拦住"
else
  # 只看 ASCII 片段：Windows 下 Python 的 stderr 是 GBK，中文断言会因编码而误判
  grep -q 'RuntimeError' "$TMP/badname.log" && grep -q 'LLM_PROFILE' "$TMP/badname.log" \
    && ok "非法档位名被拦住（路径穿越防护）" \
    || no "报错信息没有说明原因"
fi

if env LLM_PROFILE=not-exist LLM_PROFILE_DIR="$PROFILE_DIR" \
  "$PY" -c 'from app.config import settings' >"$TMP/nofile.log" 2>&1; then
  no "缺档位文件时没有报错"
else
  grep -q 'not-exist.env' "$TMP/nofile.log" && ok "缺档位文件时报错并给出路径" || no "报错信息没给出缺失路径"
fi

# ---------- 2. 缓存命中 ----------
if ! start_fake_llm; then
  no "假端点没起来，跳过缓存用例"
else
  ok "假端点已就绪 :$PORT"

  run_gen "$TMP/run1" \
    && grep -q '"cache_hits": 0' "$TMP/run1/meta.json" \
    && ok "第一次生成：cache_hits=0（真实调用）" \
    || no "第一次生成异常，见 $TMP/gen.log"

  if ls "$WORKSPACE_DIR/.cache/"*.json >/dev/null 2>&1; then
    ok "缓存落在 workspace/.cache（$(ls "$WORKSPACE_DIR/.cache/" | wc -l | tr -d ' ') 个条目）"
  else
    no "缓存目录里没有条目"
  fi

  # 关掉假端点再跑一次：能成功就说明这次真的没发请求
  stop_fake_llm
  if run_gen "$TMP/run2" && grep -qE '"cache_hits": [1-9]' "$TMP/run2/meta.json"; then
    ok "第二次生成：cache_hits=$(meta_field "$TMP/run2/meta.json" cache_hits)，且端点已关闭仍成功"
  else
    no "第二次生成没有完全命中缓存，见 $TMP/gen.log"
  fi

  # 换模型必须重新生成（不能拿旧模型的输出）
  if ! start_fake_llm; then
    no "假端点重启失败，跳过换模型用例"
  else
    out="$(env LLM_PROFILE="$PROFILE" LLM_PROFILE_DIR="$PROFILE_DIR" WORKSPACE_DIR="$WORKSPACE_DIR" \
      ENGINE_MODE=llm LLM_MODEL=another-model LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
      LLM_API_KEY=dummy-key-not-a-real-secret \
      "$PY" scripts/gen_once.py "做一个待办清单，可以增删条目" --engine llm --timeout 60 \
      --out "$TMP/run3" 2>&1)"
    if grep -q '"cache_hits": 0' "$TMP/run3/meta.json" 2>/dev/null; then
      ok "换模型后不命中缓存（缓存键含模型身份）"
    else
      no "换模型后仍然命中缓存，缓存键隔离有问题"
    fi
  fi

  # 显式关缓存
  env LLM_CACHE=0 LLM_PROFILE="$PROFILE" LLM_PROFILE_DIR="$PROFILE_DIR" \
    WORKSPACE_DIR="$WORKSPACE_DIR" ENGINE_MODE=llm \
    "$PY" scripts/gen_once.py "做一个待办清单，可以增删条目" --engine llm --timeout 60 \
    --out "$TMP/run4" >>"$TMP/gen.log" 2>&1
  grep -q '"cache_hits": 0' "$TMP/run4/meta.json" 2>/dev/null \
    && ok "LLM_CACHE=0 时不使用缓存" \
    || no "LLM_CACHE=0 仍然命中了缓存"
fi

# ---------- 3. 切换脚本的静态校验 ----------
if PROFILE_DIR="$PROFILE_DIR" DROPIN_DIR="$TMP/dropin" \
  bash scripts/switch_model.sh check "$PROFILE" >"$TMP/check.log" 2>&1; then
  ok "switch_model.sh check 通过"
else
  no "switch_model.sh check 失败：$(tail -n 3 "$TMP/check.log" | tr '\n' ' ')"
fi

if PROFILE_DIR="$PROFILE_DIR" DROPIN_DIR="$TMP/dropin" \
  bash scripts/switch_model.sh check no-such-profile >/dev/null 2>&1; then
  no "switch_model.sh check 对不存在的档位返回成功"
else
  ok "switch_model.sh check 挡住不存在的档位"
fi

printf '\n%d 通过 / %d 失败\n\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
