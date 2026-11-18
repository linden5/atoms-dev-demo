"""HTTP 路由层。

约定：路由只做参数校验与响应组装，不写业务推理逻辑。
S2 仅有 `/health`；S3 起在同一个 router 上追加 `/api/...` 路由（只加路由，不改已有签名）。
"""

from __future__ import annotations

import platform
import time
from datetime import datetime, timezone

from fastapi import APIRouter

from app import STEP, __version__

router = APIRouter()

# 进程启动时刻，用于 /health 上报 uptime
_STARTED_AT = time.monotonic()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@router.get("/health", tags=["meta"], summary="存活与版本信息")
@router.head("/health", include_in_schema=False)
async def health() -> dict:
    """存活探针：deploy.sh 探活、systemd 外部检查、前端顶栏状态灯都用它。

    同时注册 HEAD，方便运维用 `curl -I http://host:8000/health` 只取状态码与响应头。
    """
    return {
        "status": "ok",
        "version": __version__,
        "step": STEP,
        "engine": "none",
        "uptime_s": round(time.monotonic() - _STARTED_AT, 3),
        "python": platform.python_version(),
        "time": _now(),
    }
