"""HTTP 路由层。

约定：只做参数校验与响应组装，业务逻辑在 `app/tasks.py`，存储细节在 `app/db.py`。
接口契约见 docs/design.md 4.1；S3 起只加路由，不改已有签名。
"""

from __future__ import annotations

import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from app import STEP, db, tasks, __version__
from app.config import settings

router = APIRouter()

_STARTED_AT = time.monotonic()

# 预览响应的 CSP：禁止外联脚本，只允许自身、内联与 data/blob
PREVIEW_CSP = (
    "default-src 'self' 'unsafe-inline' data: blob:; "
    "img-src 'self' data: blob:; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'"
)

HTML_MEDIA_TYPE = "text/html; charset=utf-8"
MAX_PROMPT_CHARS = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- 存活 ----------


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
        "engine": tasks.engine_name(),
        "engine_mode": settings.engine_mode,
        "uptime_s": round(time.monotonic() - _STARTED_AT, 3),
        "python": platform.python_version(),
        "time": _now(),
    }


# ---------- 任务 ----------


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=MAX_PROMPT_CHARS)
    parent_id: str | None = Field(default=None, max_length=64)

    @field_validator("prompt")
    @classmethod
    def _strip(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("prompt 不能为空")
        return text


@router.post("/api/generate", status_code=202, tags=["tasks"], summary="创建生成任务")
async def generate(payload: GenerateRequest) -> JSONResponse:
    """建任务并入队，立刻返回 task_id（不等待生成）。"""
    started = time.perf_counter()

    parent_id = payload.parent_id
    if parent_id is not None and db.get_task(parent_id) is None:
        raise HTTPException(status_code=404, detail="parent task not found")

    task = await tasks.start(payload.prompt, parent_id=parent_id)
    accept_ms = round((time.perf_counter() - started) * 1000, 1)
    return JSONResponse(
        status_code=202,
        content={
            "task_id": task.id,
            "status": task.status,
            "parent_id": task.parent_id,
            "accept_ms": accept_ms,
        },
    )


@router.get("/api/tasks", tags=["tasks"], summary="任务列表")
async def list_tasks(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    items = db.list_tasks(limit=limit, offset=offset)
    return {
        "total": db.count_tasks(),
        "limit": limit,
        "offset": offset,
        "tasks": [task.summary() for task in items],
    }


@router.get("/api/tasks/{task_id}", tags=["tasks"], summary="任务详情")
async def task_detail(task_id: str) -> dict:
    task = db.get_task(task_id, with_events=True)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    payload = task.to_dict()
    payload["entry"] = tasks.entry_of(task.artifacts)
    return payload


@router.get("/api/tasks/{task_id}/versions", tags=["tasks"], summary="版本链")
async def task_versions(task_id: str) -> dict:
    """返回该任务所在版本树的全部版本（按创建时间升序，v1 最早）。

    版本链用 `parent_id` 表达，没有独立表；同一版本被修改两次会形成分支，
    这里按广度优先 + 时间排序拍平，前端用 v1…vN 展示。
    """
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    root_id = db.root_of(task_id)
    versions = db.list_versions(root_id)
    return {
        "root_id": root_id,
        "current_id": task_id,
        "total": len(versions),
        "versions": [version.summary() for version in versions],
    }


@router.post("/api/tasks/{task_id}/cancel", tags=["tasks"], summary="取消任务")
async def cancel_task(task_id: str) -> dict:
    task = await tasks.cancel(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return {"task_id": task.id, "status": task.status}


# ---------- 产物 ----------


def _entry_path(task_id: str) -> Path:
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    entry = tasks.entry_of(task.artifacts)
    if entry is None:
        raise HTTPException(status_code=404, detail="no previewable artifact")
    path = tasks.resolve_artifact(task_id, entry)
    if path is None:
        raise HTTPException(status_code=404, detail="artifact missing on disk")
    return path


@router.get("/api/tasks/{task_id}/preview", tags=["artifacts"], summary="产物主 HTML")
@router.head("/api/tasks/{task_id}/preview", include_in_schema=False)
async def preview(task_id: str) -> FileResponse:
    path = _entry_path(task_id)
    return FileResponse(
        path,
        media_type=HTML_MEDIA_TYPE,
        headers={"Content-Security-Policy": PREVIEW_CSP, "Cache-Control": "no-store"},
    )


@router.get("/api/tasks/{task_id}/files/{relative:path}", tags=["artifacts"], summary="产物中的静态文件")
@router.head("/api/tasks/{task_id}/files/{relative:path}", include_in_schema=False)
async def artifact_file(task_id: str, relative: str) -> FileResponse:
    """路径必须落在 `workspace/{task_id}/` 内，越界与不存在的文件都返回 404。"""
    if db.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")

    path = tasks.resolve_artifact(task_id, relative)
    if path is None:
        raise HTTPException(status_code=404, detail="artifact not found")

    headers = {"Cache-Control": "no-store"}
    # 预览 HTML 走同一个 CSP；其它静态资源不需要
    if path.suffix.lower() in {".html", ".htm"}:
        headers["Content-Security-Policy"] = PREVIEW_CSP
    return FileResponse(path, headers=headers)
