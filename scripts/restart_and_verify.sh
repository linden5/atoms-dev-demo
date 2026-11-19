#!/usr/bin/env bash
# 一条命令完成启动 + 校验：
#   bash scripts/restart_and_verify.sh <档位>
# 默认流程 = 同步代码与依赖（deploy.sh）→ 停旧服务 → 按档位启动 → 健康检查 → 模型连通冒烟。
# 服务未安装时（首次部署）会自动先执行 deploy.sh 装好 systemd 单元再继续。
# 档位必须显式指定（dev / strong …），启动前先打印该档位使用的模型。
# 校验任何一步失败都立即停下（非零退出），不在半可用状态继续。
#
# 面向 systemd 部署（服务器）。本地 Windows 开发机没有 systemctl，
# 直接用：LLM_PROFILE=<档位> python -m uvicorn app.main:app --port 8000
#
# 可选参数（日常都用不到）：
#   --no-deploy  跳过 deploy.sh（只切档不拉代码）
#   --full       追加最新步骤的 verify_sN.sh 全量验收
#   --no-smoke   跳过模型连通冒烟（不产生真实调用）
#
# 可覆盖的环境变量与 switch_model.sh 相同：PROFILE_DIR / SERVICE / BASE_URL / DROPIN_DIR
#
# 关于冒烟：会向厂商发一次 max_tokens=64 的真实请求（费用可忽略），
# 专门提前暴露 401 / 404 / 思考吃光输出预算这三类"服务起来了但模型用不了"的问题。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 复用 switch_model.sh 的校验、切换、探活与回滚（文件末尾有执行守卫，source 不会触发 main）
# shellcheck source=switch_model.sh
source "$SCRIPT_DIR/switch_model.sh"

PYBIN="${PYBIN:-$(command -v python3 || command -v python || true)}"

usage() {
  cat <<'USAGE'
用法：
  bash scripts/restart_and_verify.sh <档位>   # 同步代码 + 停旧服务 + 按档位启动 + 校验

档位必须显式指定（dev / strong …），启动前会打印该档位使用的模型。
可选参数：
  --no-deploy  跳过 deploy.sh（不拉代码不安装；服务未装时也不会自动安装）
  --full       追加最新步骤的 verify_sN.sh 全量验收
  --no-smoke   跳过模型连通冒烟
USAGE
}

# 模型连通冒烟：直接按档位文件向厂商发一次最小请求，不经过服务进程。
# 这样能把"服务健康"和"模型真的能用"分开验证——后者恰恰是最常见的坑。
smoke_llm() {
  # 依赖 main 里已经 source 过档位文件（LLM_* 已在环境里）
  : "${LLM_BASE_URL:?档位缺少 LLM_BASE_URL}" || return 1
  : "${LLM_MODEL:?档位缺少 LLM_MODEL}" || return 1
  : "${LLM_API_KEY:?档位缺少 LLM_API_KEY}" || return 1

  "$PYBIN" - <<'PY'
import json, os, sys, urllib.error, urllib.request

base = os.environ["LLM_BASE_URL"].rstrip("/")
url = base + "/chat/completions"
payload = {
    "model": os.environ["LLM_MODEL"],
    "messages": [{"role": "user", "content": "连通性测试：只回复两个字，可以"}],
    "max_tokens": 64,
    "stream": False,
}
if os.environ.get("LLM_REASONING_EFFORT"):
    payload["reasoning_effort"] = os.environ["LLM_REASONING_EFFORT"]
extra = (os.environ.get("LLM_EXTRA_BODY") or "").strip()
if extra:
    payload.update(json.loads(extra))

req = urllib.request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    method="POST",
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ["LLM_API_KEY"],
    },
)
try:
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
except urllib.error.HTTPError as exc:
    detail = exc.read().decode("utf-8", errors="replace")[:160]
    print(f"FAIL  HTTP {exc.code}：{detail}", file=sys.stderr)
    sys.exit(1)
except Exception as exc:  # noqa: BLE001
    print(f"FAIL  {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(1)

content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
usage = data.get("usage") or {}
reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
if not content.strip():
    print(
        f"FAIL  返回内容为空（reasoning_tokens={reasoning}，"
        f"completion={usage.get('completion_tokens')}）：调大 LLM_MAX_TOKENS，"
        "或按厂商配置思考开关（智谱 LLM_REASONING_EFFORT=low，"
        'DeepSeek LLM_EXTRA_BODY={"thinking":{"type":"disabled"}}）',
        file=sys.stderr,
    )
    sys.exit(1)
print(
    f"OK    返回模型={data.get('model')}  content={content[:12]!r}  "
    f"tokens={usage.get('total_tokens')}"
)
PY
}

json_field() {  # 从 FastAPI 的紧凑 JSON 里抠字符串字段
  sed -n "s/.*\"$2\":\"[^\"]*\".*/&/p" <<<"$1" | sed -n "s/.*\"$2\":\"\([^\"]*\)\".*/\1/p"
}

main() {
  local PROFILE="" FULL=0 SMOKE=1 DEPLOY=1 FRESH=0 health engine model prof prev verify_script

  while [ $# -gt 0 ]; do
    case "$1" in
      --full) FULL=1 ;;
      --no-smoke) SMOKE=0 ;;
      --no-deploy) DEPLOY=0 ;;
      -h | --help) usage; return 0 ;;
      -*)
        usage; die "未知参数：$1"
        ;;
      *)
        [ -z "$PROFILE" ] || die "档位只能指定一个（又收到：$1）"
        PROFILE="$1"
        ;;
    esac
    shift
  done
  # 档位必须显式指定：启动用哪个模型是大事，不允许脚本替人猜
  [ -n "$PROFILE" ] || { usage >&2; die "必须指定档位：bash scripts/restart_and_verify.sh <档位>"; }
  if [ -f "$(profile_path "$PROFILE")" ]; then
    info "档位 $PROFILE 将使用模型：$(var_value "$(profile_path "$PROFILE")" LLM_MODEL)"
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    die "没找到 systemctl：本脚本面向 systemd 部署；本地开发直接 LLM_PROFILE=<档位> python -m uvicorn app.main:app --port 8000"
  fi
  # 存在性检查与其余 systemctl 操作一样走 need_sudo：
  # 部分服务器/容器环境非 root 直连 systemd 受限，deploy.sh 用 sudo 能装能重启，
  # 裸 systemctl cat 却失败，会造成"装好了却说没有"的误报
  local SUDO_CMD=""
  SUDO_CMD="$(need_sudo)"
  if ! $SUDO_CMD systemctl cat "$SERVICE" >/dev/null 2>&1; then
    if [ "$DEPLOY" -eq 1 ]; then
      warn "systemd 里没有 $SERVICE 服务：自动执行 deploy.sh 首次安装"
      printf '\n== 0/4 首次安装（deploy.sh）==\n'
      bash "$SCRIPT_DIR/deploy.sh" || die "deploy.sh 失败，服务未安装"
      $SUDO_CMD systemctl cat "$SERVICE" >/dev/null 2>&1 || die "deploy.sh 执行后仍找不到 $SERVICE 服务单元"
      FRESH=1
    else
      die "systemd 里没有 $SERVICE 服务：先执行 bash scripts/deploy.sh 安装（或去掉 --no-deploy 让本脚本自动安装）"
    fi
  fi
  [ -n "$PYBIN" ] || die "找不到 python3/python：模型冒烟需要它"

  prev="$(get_current_profile)"

  if [ "$DEPLOY" -eq 1 ] && [ "$FRESH" -eq 0 ]; then
    printf '\n== 0/4 同步代码与依赖（deploy.sh）==\n'
    bash "$SCRIPT_DIR/deploy.sh" || die "deploy.sh 失败，未切档"
  fi

  # 1) 校验 + 停旧启新 + 探活（失败自动回滚，这一步在 switch_model.sh 里实现）
  printf '\n== 1/4 切换档位（%s → %s）==\n' "${prev:-（unit 默认）}" "$PROFILE"
  cmd_use "$PROFILE" yes

  # 2) 健康断言：档位名、引擎模式、模型名都要对上，防止"以为切了其实没切"。
  #    先把档位文件 source 进来：ENGINE_MODE 等声明要参与下面的期望比对
  printf '\n== 2/4 健康检查 ==\n'
  set -a
  # shellcheck disable=SC1090
  . "$(profile_path "$PROFILE")"
  set +a
  health="$(curl -fsS --max-time 5 "${BASE_URL}/health" 2>/dev/null)" || die "拉取 ${BASE_URL}/health 失败"
  echo "$health" | grep -q '"status":"ok"' || die "/health status 不是 ok：$health"
  prof="$(json_field "$health" profile)"
  [ "$prof" = "$PROFILE" ] || die "/health profile=$prof，期望 $PROFILE"
  engine="$(json_field "$health" engine)"
  # 档位文件里声明的引擎模式决定期望值；auto 时只要不是 mock 就行（LLM_* 已配齐）
  case "${ENGINE_MODE:-auto}" in
    mock) [ "$engine" = "mock" ] || die "档位声明 ENGINE_MODE=mock，但 engine=$engine" ;;
    llm) [ "$engine" = "llm" ] || die "档位声明 ENGINE_MODE=llm，但 engine=$engine" ;;
    metagpt) [ "$engine" = "metagpt" ] || die "档位声明 ENGINE_MODE=metagpt，但 engine=$engine" ;;
    *) [ "$engine" != "mock" ] || die "auto 模式下 engine=mock：LLM_* 已配齐却降级了，看 journalctl -u $SERVICE" ;;
  esac
  if [ "$engine" = "llm" ]; then
    model="$(json_field "$health" model)"
    [ -n "$model" ] || die "engine=llm 但 /health 没有回显模型名"
    ok "engine=llm  model=$model  profile=$prof"
  else
    ok "engine=$engine  profile=$prof"
  fi

  # 3) 模型连通冒烟：不经过服务进程，直接按档位文件问一次厂商
  if [ "$SMOKE" -eq 1 ] && [ "$engine" != "mock" ] && [ "$engine" != "metagpt" ]; then
    printf '\n== 3/4 模型连通冒烟（一次真实调用，费用忽略不计）==\n'
    smoke_llm "$PROFILE" || {
      [ -n "$prev" ] && warn "回滚到切换前：bash scripts/switch_model.sh use $prev"
      die "模型冒烟失败"
    }
  elif [ "$SMOKE" -eq 0 ]; then
    printf '\n== 3/4 模型连通冒烟（--no-smoke 跳过）==\n'
  else
    printf '\n== 3/4 模型连通冒烟（%s 引擎无需冒烟，跳过）==\n' "$engine"
  fi

  # 4) 可选：全量验收
  if [ "$FULL" -eq 1 ]; then
    verify_script="${VERIFY_SCRIPT:-$(ls "$SCRIPT_DIR"/verify_s*.sh 2>/dev/null | sort -V | tail -n 1 | xargs -r basename)}"
    [ -n "$verify_script" ] || die "找不到 verify_sN.sh"
    printf '\n== 4/4 全量验收（%s）==\n' "$verify_script"
    BASE_URL="$BASE_URL" bash "$SCRIPT_DIR/$verify_script" || {
      [ -n "$prev" ] && warn "回滚到切换前：bash scripts/switch_model.sh use $prev"
      die "验收未通过：$verify_script"
    }
  fi

  printf '\n== 完成：档位=%s 引擎=%s 模型=%s 地址=%s ==\n' "$PROFILE" "$engine" "${model:--}" "$BASE_URL"
  info "实时日志：journalctl -u $SERVICE -f"
  info "回滚方式：bash scripts/switch_model.sh use ${prev:-<旧档位>}"
  return 0
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
