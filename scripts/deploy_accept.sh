#!/usr/bin/env bash
# 验收环境一条命令部署（参数已写死，不要在这里改端口/档位，要改去 deploy.sh 的实例预设）：
#   bash scripts/deploy_accept.sh           # 拉代码 -> 切档 -> 健康检查 -> 模型冒烟
#   bash scripts/deploy_accept.sh --full    # 追加全量验收（参数原样透传给 restart_and_verify.sh）
# 固定值：分支 main / 目录 ~/atoms-dev-demo / 端口 8000 / 服务 atoms / 档位 strong
set -uo pipefail
export INSTANCE=accept SERVICE=atoms BASE_URL="http://127.0.0.1:8000"
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/restart_and_verify.sh" strong "$@"
