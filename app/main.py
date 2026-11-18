"""应用入口。

职责：配置日志 → 初始化数据库 → 注册路由 → 挂载 `web/` 静态前端 → 启停钩子。

启动：`uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1`
单 worker 是硬性约束：任务队列与并发信号量都在进程内，多 worker 会导致状态不一致。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import STEP, __version__, db, security, tasks
from app.api import router as api_router
from app.config import settings

BASE_DIR = settings.base_dir
WEB_DIR = BASE_DIR / "web"

log = logging.getLogger("atoms")

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s · %(message)s"


def configure_logging() -> None:
    """stdout 交给 journald，同时落一份到 logs/app.log（滚动 2MB × 3）。"""
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    file_handler = RotatingFileHandler(
        settings.log_dir / "app.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
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

    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    db.init_db()
    interrupted = tasks.interrupt_stale_tasks()

    log.info(
        "启动 atoms demo · step=%s · version=%s · engine=%s · web=%s",
        STEP,
        __version__,
        tasks.engine_name(),
        WEB_DIR,
    )
    if interrupted:
        log.warning("有 %d 个任务因上次进程退出被标记为 failed", interrupted)

    try:
        yield
    finally:
        await tasks.shutdown()
        db.close()
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

security.install(app)
app.include_router(api_router)

# 挂载放在路由之后：显式路由优先匹配，其余交给静态文件
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
