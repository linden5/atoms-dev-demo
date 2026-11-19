"""HTTP 路由层。

约定：只做参数校验与响应组装，业务逻辑在 `app/tasks.py`，存储细节在 `app/db.py`。
接口契约见 docs/design.md 4.1；S3 起只加路由，不改已有签名。
"""

from __future__ import annotations

import io
import platform
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from app import STEP, db, llm_cache, models, tasks, __version__
from app.config import ENGINE_MODE_LLM, ENGINE_MODE_MOCK, settings

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
    engine = tasks.engine_name()
    return {
        "status": "ok",
        "version": __version__,
        "step": STEP,
        "engine": engine,
        # 只回显模型名，不回显 Key 与端点地址
        "model": settings.llm_model if engine == ENGINE_MODE_LLM else "",
        # 能力档只报名字，档位文件路径与其中的密钥一律不下发
        "profile": settings.llm_profile,
        "engine_mode": settings.engine_mode,
        # MOCK 不调模型，缓存不会被用到；如实报 enabled=false，避免误以为命中过
        "cache": llm_cache.summary(active=engine != ENGINE_MODE_MOCK and not settings.mock_engine),
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


# ---------- 会话（多轮对话） ----------


class SessionCreateRequest(BaseModel):
    title: str = Field(default="", max_length=120)


class MessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=MAX_PROMPT_CHARS)

    @field_validator("content")
    @classmethod
    def _strip(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("content 不能为空")
        return text


@router.post("/api/sessions", status_code=201, tags=["sessions"], summary="创建会话")
async def create_session(payload: SessionCreateRequest | None = None) -> dict:
    session = db.create_session(payload.title if payload else "")
    return {"session_id": session.id, "session": session.to_dict()}


@router.get("/api/sessions", tags=["sessions"], summary="会话列表")
async def list_sessions(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    items = db.list_sessions(limit=limit, offset=offset)
    return {
        "total": db.count_sessions(),
        "limit": limit,
        "offset": offset,
        "sessions": [session.to_dict() for session in items],
    }


@router.get("/api/sessions/{session_id}", tags=["sessions"], summary="会话详情")
async def session_detail(session_id: str) -> dict:
    """会话 + 消息流 + 消息引用到的任务快照。

    任务快照内联在这里，前端刷新一次就能把气泡、阶段、版本卡片全部画出来；
    只有最后一条任务带 events（阶段耗时要用），切换版本时再按需拉 `/api/tasks/{id}`。
    """
    session = db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    messages = db.list_messages(session_id)
    snapshots: dict[str, dict] = {}
    for message in messages:
        task_id = message.task_id
        if not task_id or task_id in snapshots:
            continue
        task = db.get_task(task_id, with_events=task_id == session.last_task_id)
        if task is None:
            continue
        payload = task.to_dict()
        payload["entry"] = tasks.entry_of(task.artifacts)
        snapshots[task_id] = payload

    return {
        "session": session.to_dict(),
        "messages": [message.to_dict() for message in messages],
        "tasks": list(snapshots.values()),
    }


@router.post(
    "/api/sessions/{session_id}/messages",
    status_code=202,
    tags=["sessions"],
    summary="发送消息（可能追问，也可能直接生成）",
)
async def post_message(session_id: str, payload: MessageRequest) -> dict:
    try:
        result = await tasks.start_turn(session_id, payload.content)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return result.to_dict()


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


# ---------- 发布 / 分享 ----------
#
# 语义（对齐 docs/sprint-s8.md 与 Atoms 的 Publish 面板）：
# - Preview 是工作副本，`/p/{slug}` 才是"访客能打开的线上版本"；
# - 同一个会话视为同一个应用，重复发布会把地址指向新版本（Update 语义）；
# - 地址全局唯一，被别的应用占用时返回 409，不静默改用户的地址。

PUBLIC_HEADERS = {
    "Content-Security-Policy": PREVIEW_CSP,
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex",
}


class PublishRequest(BaseModel):
    slug: str = Field(default="", max_length=40)
    title: str = Field(default="", max_length=120)


def _publish_payload(publish: models.Publish, *, updated: bool) -> dict:
    data = publish.to_dict()
    data.update({"created": not updated, "updated": updated})
    return data


@router.post("/api/tasks/{task_id}/publish", tags=["publish"], summary="发布或更新公开链接")
async def publish_task(task_id: str, body: PublishRequest | None = None) -> JSONResponse:
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status != models.STATUS_SUCCEEDED:
        raise HTTPException(status_code=409, detail="只有已完成的版本才能发布")
    if tasks.entry_of(task.artifacts) is None:
        raise HTTPException(status_code=409, detail="该版本没有可预览的 HTML")

    slug = models.normalize_slug((body.slug if body else "") or "")
    if not slug:
        slug = models.suggest_slug()
    elif not models.valid_slug(slug):
        raise HTTPException(
            status_code=400,
            detail="地址只能用小写字母、数字和连字符，长度 6–30，且不能以连字符开头或结尾",
        )

    session_id = db.session_of_task(task.id)
    title = (body.title if body else "").strip() or task.prompt[:60]
    existing = db.get_publish(slug)

    if existing is not None:
        if existing.task_id == task.id:
            return JSONResponse(_publish_payload(existing, updated=False), status_code=200)
        same_app = bool(existing.session_id) and existing.session_id == session_id
        if not same_app:
            raise HTTPException(status_code=409, detail="这个地址已被其他应用占用，请换一个")
        db.update_publish_target(slug, task.id, title)
        refreshed = db.get_publish(slug) or existing
        return JSONResponse(_publish_payload(refreshed, updated=True), status_code=200)

    stamp = db.now()
    publish = models.Publish(
        slug=slug,
        task_id=task.id,
        session_id=session_id,
        title=title,
        created_at=stamp,
        updated_at=stamp,
    )
    try:
        db.create_publish(publish)
    except sqlite3.IntegrityError:  # 并发下同 slug：当作冲突处理，不 500
        raise HTTPException(status_code=409, detail="这个地址刚被占用，请换一个") from None
    return JSONResponse(_publish_payload(publish, updated=False), status_code=201)


@router.get("/api/tasks/{task_id}/publish", tags=["publish"], summary="该版本所在应用的公开地址")
async def publish_of_task(task_id: str) -> dict:
    if db.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    session_id = db.session_of_task(task_id)
    publish = (
        db.get_publish_by_session(session_id) if session_id else db.get_publish_by_task(task_id)
    )
    return {"publish": publish.to_dict() if publish else None}


@router.get("/api/publishes", tags=["publish"], summary="已发布的公开链接列表")
async def list_publishes(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    items = [p.to_dict() for p in db.list_publishes(limit=limit)]
    return {"total": len(items), "publishes": items}


@router.delete("/api/publishes/{slug}", tags=["publish"], summary="取消发布（Unpublish）")
async def unpublish(slug: str) -> dict:
    if not db.delete_publish(models.normalize_slug(slug)):
        raise HTTPException(status_code=404, detail="publish not found")
    return {"slug": slug, "deleted": True}


@router.get("/api/tasks/{task_id}/zip", tags=["artifacts"], summary="导出产物 ZIP")
async def export_zip(task_id: str) -> Response:
    """Export 入口：整包下载产物，用于"离开平台也能跑"。"""
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if not task.artifacts:
        raise HTTPException(status_code=404, detail="no artifact")

    total = sum(artifact.size for artifact in task.artifacts)
    if total > settings.max_artifact_bytes:
        raise HTTPException(status_code=413, detail="产物过大，暂不支持打包下载")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for artifact in task.artifacts:
            path = tasks.resolve_artifact(task_id, artifact.path)
            if path is not None:
                archive.write(path, artifact.path)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{task_id}.zip"',
            "Cache-Control": "no-store",
        },
    )


_PUBLIC_404 = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>页面不存在</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0f14;
color:#e6edf3;font-family:system-ui,"PingFang SC",sans-serif;text-align:center}
a{color:#22d3ee}</style></head><body><main><h1>这个地址还没有发布内容</h1>
<p>可能是链接写错了，或者作者已经取消发布。</p><p><a href="/">回到 Atoms Demo</a></p>
</main></body></html>"""


def _public_404() -> HTMLResponse:
    return HTMLResponse(_PUBLIC_404, status_code=404, headers={"Cache-Control": "no-store"})


def _published_task(slug: str) -> models.Task:
    publish = db.get_publish(models.normalize_slug(slug))
    if publish is None:
        raise HTTPException(status_code=404, detail="not found")
    task = db.get_task(publish.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="not found")
    return task


@router.get("/p/{slug}", summary="已发布应用的入口页")
@router.head("/p/{slug}", include_in_schema=False)
async def public_app(slug: str) -> Response:
    try:
        task = _published_task(slug)
        entry = tasks.entry_of(task.artifacts)
        path = tasks.resolve_artifact(task.id, entry) if entry else None
    except HTTPException:
        return _public_404()
    if path is None:
        return _public_404()
    return FileResponse(path, media_type=HTML_MEDIA_TYPE, headers=PUBLIC_HEADERS)


@router.get("/p/{slug}/files/{relative:path}", summary="已发布应用的静态资源")
@router.head("/p/{slug}/files/{relative:path}", include_in_schema=False)
async def public_asset(slug: str, relative: str) -> Response:
    try:
        task = _published_task(slug)
    except HTTPException:
        return _public_404()
    path = tasks.resolve_artifact(task.id, relative)
    if path is None:
        raise HTTPException(status_code=404, detail="not found")
    headers = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}
    if path.suffix.lower() in {".html", ".htm"}:
        headers["Content-Security-Policy"] = PREVIEW_CSP
    return FileResponse(path, headers=headers)
