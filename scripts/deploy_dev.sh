#!/usr/bin/env bash
# 自测环境一条命令部署（参数已写死，不要在这里改端口/档位，要改去 deploy.sh 的实例预设）：
#   bash scripts/deploy_dev.sh              # 拉代码 -> 切档 -> 健康检查 -> 模型冒烟
#   bash scripts/deploy_dev.sh --full       # 追加全量验收（参数原样透传给 restart_and_verify.sh）
# 固定值：分支 dev / 目录 ~/atoms-dev-demo-dev / 端口 8001 / 服务 atoms-dev / 档位 dev
set -uo pipefail
export INSTANCE=dev SERVICE=atoms-dev BASE_URL="http://127.0.0.1:8001"
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/restart_and_verify.sh" dev "$@"
