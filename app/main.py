"""应用入口。

职责：配置日志 → 注册路由 → 挂载 `web/` 静态前端 → 启停钩子。

启动：`uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1`
单 worker 是硬性约束：任务队列与并发信号量都在进程内（S3 起）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import STEP, __version__
from app.api import router as api_router

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
LOG_DIR = BASE_DIR / "logs"

log = logging.getLogger("atoms")

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s · %(message)s"


def configure_logging() -> None:
    """stdout 交给 journald，同时落一份到 logs/app.log（滚动 2MB × 3）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    file_handler = RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handlers.append(file_handler)
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    if not WEB_DIR.is_dir():
        raise RuntimeError(f"前端目录不存在：{WEB_DIR}")
    log.info("启动 atoms demo · step=%s · version=%s · web=%s", STEP, __version__, WEB_DIR)
    try:
        yield
    finally:
        log.info("停止 atoms demo")


app = FastAPI(
    title="Atoms Demo",
    version=__version__,
    description="输入自然语言需求，由智能体生成可运行的网页应用。",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.include_router(api_router)

# 挂载放在路由之后：显式路由优先匹配，其余交给静态文件
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
