#!/usr/bin/env bash
# 能力档切换：一份档位文件 = 一套 LLM 配置，切换后重启服务并自动探活。
#
# 为什么不在应用里热切换：引擎在进程启动时读配置，运行中换文件会出现
# "以为切了其实没切"，所以这里统一走「改 systemd → 重启 → 探活」。
#
# 用法：
#   bash scripts/switch_model.sh list               列出已有档位
#   bash scripts/switch_model.sh show               看当前生效的档位
#   bash scripts/switch_model.sh check <档位>       只校验档位文件，不动服务
#   bash scripts/switch_model.sh use <档位>         切换并重启（失败自动回滚）
#   bash scripts/switch_model.sh use <档位> --no-restart   只写 drop-in
#   bash scripts/switch_model.sh init <档位>        生成档位文件模板（权限 600）
#
# 可覆盖的环境变量：
#   PROFILE_DIR   档位目录，默认 ~/.atoms/llm（可指到 /etc/atoms/llm）
#   SERVICE       systemd 服务名，默认 atoms
#   SERVICE_USER  服务运行用户，默认当前用户（用于提示文件权限）
#   BASE_URL      探活地址，默认 http://127.0.0.1:8000
#   DROPIN_DIR    drop-in 目录，默认 /etc/systemd/system
#
# 安全约定：
# 1. 档位文件里放密钥，权限必须 600；本脚本从不打印文件内容，只打印变量名；
# 2. 密钥不写进 systemd unit（unit 里的 Environment= 能被 systemctl show 读到），
#    只通过 EnvironmentFile 注入；
# 3. 档位名只允许字母数字下划线短横线，避免被拼成 ../ 之类的路径。
set -uo pipefail

SERVICE="${SERVICE:-atoms}"
PROFILE_DIR="${PROFILE_DIR:-$HOME/.atoms/llm}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
BASE_URL="${BASE_URL%/}"
DROPIN_DIR="${DROPIN_DIR:-/etc/systemd/system}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"

REQUIRED_VARS=(LLM_BASE_URL LLM_MODEL LLM_API_KEY)

ok() { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
bad() { printf '  \033[31mERROR\033[0m %s\n' "$1"; }
info() { printf '  \033[36m···\033[0m  %s\n' "$1"; }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; }
die() { bad "$1"; exit 1; }

dropin_path() { printf '%s/%s.service.d/llm-profile.conf' "$DROPIN_DIR" "$SERVICE"; }
profile_path() { printf '%s/%s.env' "$PROFILE_DIR" "$1"; }

valid_name() { printf '%s' "$1" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$'; }

# 当前生效的档位名（从 drop-in 里读；没有 drop-in 说明还在用 unit 默认配置）
get_current_profile() {
  local dropin
  dropin="$(dropin_path)"
  [ -f "$dropin" ] || { printf ''; return 0; }
  grep -oE 'Environment=LLM_PROFILE=[^[:space:]]+' "$dropin" | head -n 1 | cut -d= -f3
}

need_sudo() {
  if [ "$(id -u)" -eq 0 ]; then
    printf ''
  else
    printf 'sudo'
  fi
}

# 读取档位文件里的变量名（不读值，避免把密钥打到终端）
var_names() {
  grep -E '^[A-Z][A-Z0-9_]*=' "$1" | cut -d= -f1 | sort -u
}

var_value() {
  grep -E "^$2=" "$1" | head -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

cmd_list() {
  printf '\n档位目录：%s\n\n' "$PROFILE_DIR"
  if [ ! -d "$PROFILE_DIR" ]; then
    info '目录还不存在，用 init <档位> 生成第一份'
    return 0
  fi
  local found=0 name
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    name="$(basename "$file" .env)"
    found=1
    printf '  %-16s %s\n' "$name" "$(stat -c '%A %U:%G' "$file" 2>/dev/null || echo '?')"
  done < <(find "$PROFILE_DIR" -maxdepth 1 -name '*.env' -type f 2>/dev/null | sort)
  [ "$found" -eq 1 ] || info '还没有任何档位文件'
  printf '\n'
}

cmd_show() {
  local dropin
  dropin="$(dropin_path)"
  printf '\n当前生效配置（%s）\n\n' "$dropin"
  if [ ! -f "$dropin" ]; then
    info '没有 drop-in，说明在用部署时的默认配置'
    return 0
  fi
  # drop-in 里只有档位名与路径，没有密钥，可以安全展示
  sed -e 's/^/  /' "$dropin"
  printf '\n'
}

cmd_check() {
  local name="$1" file failures=0 key value names
  valid_name "$name" || die "档位名非法：$name（只允许字母数字下划线短横线，最长 32）"
  file="$(profile_path "$name")"
  [ -f "$file" ] || die "档位文件不存在：$file"

  printf '\n校验档位 %s（%s）\n\n' "$name" "$file"

  names="$(var_names "$file")"
  for key in "${REQUIRED_VARS[@]}"; do
    if printf '%s\n' "$names" | grep -qx "$key"; then
      value="$(var_value "$file" "$key")"
      if [ -z "$value" ] || printf '%s' "$value" | grep -q 'replace-me'; then
        bad "$key 还是占位值"; failures=$((failures + 1))
      else
        ok "$key 已填写（长度 ${#value}）"
      fi
    else
      bad "缺少 $key"; failures=$((failures + 1))
    fi
  done

  # 模型名不是密钥，直接回显：切换档位时最需要的确认就是这个
  info "将使用模型：$(var_value "$file" LLM_MODEL)"

  local mode
  mode="$(stat -c '%a' "$file" 2>/dev/null || echo '')"
  case "$mode" in
    600 | 400) ok "文件权限 $mode" ;;
    '') warn '无法读取文件权限（非 Linux？）' ;;
    *) warn "文件权限是 $mode，建议 chmod 600（同机其他用户可读）" ;;
  esac

  local owner
  owner="$(stat -c '%U' "$file" 2>/dev/null || echo '')"
  if [ "$owner" = "root" ]; then
    # systemd 由 PID1 读取 EnvironmentFile，属主 root + 600 是可用且更严的形态
    ok "属主 root：由 systemd 注入变量，应用不需要读该文件"
  elif [ -n "$owner" ] && [ "$owner" != "$SERVICE_USER" ]; then
    warn "属主是 $owner，服务用户 $SERVICE_USER 读不到；systemd 注入无妨，但应用自行装载档位会失败"
  fi

  # base_url 是否要带版本路径因厂而异，不能一律要求 /v1：
  # 智谱是 /api/paas/v4（加了 /v1 会 404），OpenAI/DeepSeek 等才要 /v1。
  local base
  base="$(var_value "$file" LLM_BASE_URL)"
  if printf '%s' "$base" | grep -q '/chat/completions'; then
    # 最常见的 404 原因：把完整接口地址当成 base_url，引擎又拼了一次
    bad "LLM_BASE_URL 不要包含 /chat/completions（引擎会自动拼接，重复会 404）"
    failures=$((failures + 1))
  fi
  case "$base" in
    *bigmodel.cn*)
      # 智谱的版本段是 /v4，不是 /v1；判断时要排除 /v4，否则正确配置也会被误报
      if printf '%s' "$base" | grep -qE '/v[0-9]+/?$' && ! printf '%s' "$base" | grep -qE '/v4/?$'; then
        warn '智谱端点不要带 /v1，正确形如 https://open.bigmodel.cn/api/paas/v4'
      else
        ok 'LLM_BASE_URL 形态符合智谱约定（/api/paas/v4，不带 /v1）'
      fi
      ;;
    *deepseek.com*)
      # DeepSeek 官方 base_url 就是 /，/v1 只是兼容别名，两种写法都对
      ok 'LLM_BASE_URL 合法（DeepSeek 的 /v1 可省略）'
      ;;
    *)
      if ! printf '%s' "$base" | grep -qE '/v[0-9]+/?$'; then
        warn 'LLM_BASE_URL 通常以 /v1 结尾（OpenAI/月之暗面/硅基流动）；DeepSeek 可省略，智谱是 /api/paas/v4'
      fi
      ;;
  esac

  if grep -qE '^ENGINE_MODE=' "$file"; then
    warn "档位里写了 ENGINE_MODE=$(var_value "$file" ENGINE_MODE)，它会覆盖 unit 的设置；不需要就不要写"
  fi

  printf '\n'
  [ "$failures" -eq 0 ] || die "档位 $name 校验未通过（$failures 项）"
  ok "档位 $name 校验通过"
  return 0
}

write_dropin() {
  local name="$1" file dropin tmp sudo_cmd
  file="$(profile_path "$name")"
  dropin="$(dropin_path)"
  sudo_cmd="$(need_sudo)"

  tmp="$(mktemp)"
  cat >"$tmp" <<EOF
# 由 scripts/switch_model.sh 生成，手工改动会在下次切换时被覆盖
[Service]
Environment=LLM_PROFILE=$name
Environment=LLM_PROFILE_DIR=$PROFILE_DIR
EnvironmentFile=$file
EOF

  # shellcheck disable=SC2086
  $sudo_cmd mkdir -p "$(dirname "$dropin")" || die "无法创建 $DROPIN_DIR/$SERVICE.service.d"
  # shellcheck disable=SC2086
  $sudo_cmd cp "$tmp" "$dropin" || { rm -f "$tmp"; die "写入 $dropin 失败"; }
  rm -f "$tmp"
  ok "已写入 drop-in：$dropin"
}

wait_health() {
  local name="$1" health attempt
  for attempt in $(seq 1 20); do
    health="$(curl -fsS --max-time 3 "${BASE_URL}/health" 2>/dev/null || true)"
    if printf '%s' "$health" | grep -q "\"profile\":\"${name}\""; then
      ok "服务已生效：${BASE_URL}/health 报告 profile=${name}"
      return 0
    fi
    sleep 1
  done
  return 1
}

cmd_use() {
  local name="$1" restart="${2:-yes}" dropin backup sudo_cmd
  cmd_check "$name" || exit 1

  dropin="$(dropin_path)"
  backup=""
  if [ -f "$dropin" ]; then
    backup="$(mktemp)"
    cp "$dropin" "$backup" 2>/dev/null || true
  fi

  write_dropin "$name"

  if [ "$restart" = "no" ]; then
    info '已写入但未重启（--no-restart）：systemctl daemon-reload && systemctl restart '"$SERVICE"
    return 0
  fi

  sudo_cmd="$(need_sudo)"
  # shellcheck disable=SC2086
  $sudo_cmd systemctl daemon-reload || die 'systemctl daemon-reload 失败'
  # shellcheck disable=SC2086
  $sudo_cmd systemctl restart "$SERVICE" || {
    bad "重启 $SERVICE 失败，回滚上一份配置"
    rollback_dropin "$backup"
    exit 1
  }

  if wait_health "$name"; then
    [ -n "$backup" ] && rm -f "$backup"
    printf '\n'
    ok "已切换到档位 $name"
    return 0
  fi

  bad "切换后 ${BASE_URL}/health 没报告 profile=${name}，自动回滚"
  rollback_dropin "$backup"
  exit 1
}

rollback_dropin() {
  local backup="$1" dropin sudo_cmd
  dropin="$(dropin_path)"
  sudo_cmd="$(need_sudo)"
  if [ -n "$backup" ] && [ -f "$backup" ]; then
    # shellcheck disable=SC2086
    $sudo_cmd cp "$backup" "$dropin" && info '已恢复切换前的 drop-in'
    rm -f "$backup"
  else
    # shellcheck disable=SC2086
    $sudo_cmd rm -f "$dropin" && info '已移除本次写入的 drop-in'
  fi
  # shellcheck disable=SC2086
  $sudo_cmd systemctl daemon-reload || true
  # shellcheck disable=SC2086
  $sudo_cmd systemctl restart "$SERVICE" || true
  bad "已回滚到切换前的配置，请查 journalctl -u $SERVICE -n 50 --no-pager"
}

cmd_init() {
  local name="$1" file
  valid_name "$name" || die "档位名非法：$name（只允许字母数字下划线短横线，最长 32）"
  file="$(profile_path "$name")"
  [ -e "$file" ] && die "档位文件已存在：$file（先看 show，确认要覆盖再手工改）"

  mkdir -p "$PROFILE_DIR" || die "无法创建目录 $PROFILE_DIR"
  umask 077
  cat >"$file" <<EOF
# 能力档：$name
# 这里放的是密钥，权限 600、不要进版本控制。
# 刻意不写 ENGINE_MODE：unit 里的 ENGINE_MODE= 会被这里覆盖（EnvironmentFile 后应用），
# 容易把 MOCK 兜底顶回真实引擎。不写则按 auto 推断：LLM_* 配齐就用 LLM。
# ENGINE_MODE=llm
LLM_BASE_URL=https://replace-me/v1
LLM_MODEL=replace-me
LLM_API_KEY=sk-replace-me
LLM_MAX_TOKENS=8000
LLM_TEMPERATURE=0.6
EOF
  chmod 600 "$file"
  ok "已生成模板：$file"
  info '填好三项必填后：bash scripts/switch_model.sh check '"$name"
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    list) cmd_list ;;
    show) cmd_show ;;
    check) [ $# -ge 2 ] || die '用法：switch_model.sh check <档位>'; cmd_check "$2" ;;
    use)
      [ $# -ge 2 ] || die '用法：switch_model.sh use <档位> [--no-restart]'
      if [ "${3:-}" = "--no-restart" ]; then cmd_use "$2" no; else cmd_use "$2" yes; fi
      ;;
    init) [ $# -ge 2 ] || die '用法：switch_model.sh init <档位>'; cmd_init "$2" ;;
    *)
      cat <<'USAGE'
用法：
  bash scripts/switch_model.sh list                列出已有档位
  bash scripts/switch_model.sh show                看当前生效的档位
  bash scripts/switch_model.sh check <档位>        只校验，不动服务
  bash scripts/switch_model.sh use <档位>          切换并重启（失败自动回滚）
  bash scripts/switch_model.sh use <档位> --no-restart
  bash scripts/switch_model.sh init <档位>         生成档位文件模板

环境变量：PROFILE_DIR / SERVICE / SERVICE_USER / BASE_URL / DROPIN_DIR
USAGE
      [ -z "$cmd" ] || exit 2
      ;;
  esac
}

# 只在直接执行时才进入子命令分发；被其他脚本 source 时只暴露函数，便于复用
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
