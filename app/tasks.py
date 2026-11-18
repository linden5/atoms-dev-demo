"""任务编排层：状态机、并发控制、超时、取消、产物扫描。

设计要点（对应 docs/design.md 4.2 / 4.4）：
- 状态机 `pending → running → succeeded | failed | cancelled`，终态不可回退（由 db.set_status 保证）；
- 并发用进程内 `asyncio.Semaphore(MAX_CONCURRENCY)`，默认为 1：2G 内存下并行跑两次会 OOM；
- 超时用 `asyncio.wait_for(TASK_TIMEOUT)`，超时后引擎任务被取消并标记失败；
- 产物结构由 `scan_artifacts()` 统一解释，引擎不承诺文件名。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app import db, models
from app.config import ENGINE_MODE_METAGPT, ENGINE_MODE_MOCK, settings
from app.engine.base import Engine
from app.engine.mock_engine import MockEngine

log = logging.getLogger("atoms.tasks")

# 扫描时跳过的目录：生成物里出现这些既无意义也可能很大
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv"}
# 产物体积上限（单文件 / 单任务），防止异常产物把磁盘写满
ERROR_MAX_CHARS = 500

_semaphore: asyncio.Semaphore | None = None
_engine: Engine | None = None
_running: dict[str, asyncio.Task] = {}


class NoPreviewArtifact(RuntimeError):
    """产物目录里没有可预览的 HTML。"""


class ArtifactTooLarge(RuntimeError):
    """产物超过单任务体积上限。"""


# ---------- 引擎选择 ----------


def get_engine() -> Engine:
    """引擎懒加载：MOCK 与真实引擎同契约，只有这里知道具体实现类。

    `MOCK_ENGINE` 三态（见 `app/config.py`）：
    - `1`：强制 MOCK，用于演示兜底与本地回归；
    - `0`：强制 MetaGPT，装不上就抛 RuntimeError（不静默降级，避免"以为在用真模型"）；
    - 留空：auto，能导入 metagpt 就用真实引擎，否则降级 MOCK 并记一条 warning。
    """
    global _engine
    if _engine is not None:
        return _engine

    if settings.engine_mode == ENGINE_MODE_MOCK:
        _engine = MockEngine()
        return _engine

    # 只在函数内导入：模块本身不依赖 metagpt，没装依赖的环境也能启动服务
    from app.engine.metagpt_engine import MetaGPTEngine, metagpt_available

    if settings.engine_mode == ENGINE_MODE_METAGPT:
        _engine = MetaGPTEngine()
        return _engine

    available, detail = metagpt_available()
    if available:
        _engine = MetaGPTEngine()
    else:
        log.warning("未检测到 MetaGPT（%s），自动降级为 MOCK 引擎", detail)
        _engine = MockEngine()
    return _engine


def engine_name() -> str:
    try:
        return get_engine().name
    except RuntimeError:
        return "unavailable"


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.max_concurrency)
    return _semaphore


# ---------- 创建与调度 ----------


def _new_task_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"t_{stamp}_{secrets.token_hex(2)}"


async def start(prompt: str, parent_id: str | None = None) -> models.Task:
    """建任务并入队，立刻返回（不等生成）。"""
    task_id = _new_task_id()
    workdir = settings.workspace_dir / task_id
    workdir.mkdir(parents=True, exist_ok=True)

    stamp = db.now()
    task = models.Task(
        id=task_id,
        prompt=prompt,
        status=models.STATUS_PENDING,
        workdir=str(workdir),
        created_at=stamp,
        updated_at=stamp,
        parent_id=parent_id,
    )
    db.create_task(task)
    db.add_event(task_id, models.STATUS_PENDING)

    handle = asyncio.create_task(_run(task_id), name=f"task:{task_id}")
    _running[task_id] = handle
    log.info("任务入队 id=%s parent=%s prompt=%r", task_id, parent_id, prompt[:80])
    return task


async def _run(task_id: str) -> None:
    started = time.perf_counter()
    task = db.get_task(task_id)
    if task is None:  # 理论上不会发生；防御性返回
        return

    workdir = Path(task.workdir)
    try:
        # 排队期间可能已被取消，进入临界区前再确认一次
        async with _get_semaphore():
            if not db.set_status(task_id, models.STATUS_RUNNING):
                log.info("任务在排队中被取消 id=%s", task_id)
                return
            db.add_event(task_id, models.STATUS_RUNNING)
            engine = get_engine()
            await asyncio.wait_for(
                _invoke_engine(engine, task, workdir),
                timeout=settings.task_timeout,
            )
        _finish_success(task_id, workdir)

    except asyncio.TimeoutError:
        _finish_failure(task_id, f"timeout after {settings.task_timeout}s")
    except asyncio.CancelledError:
        current = db.get_task(task_id)
        # 用户主动取消时状态已是 cancelled；进程退出导致的取消记为 interrupted
        if current is not None and current.status != models.STATUS_CANCELLED:
            _finish_failure(task_id, "interrupted")
        raise
    except Exception as exc:  # 引擎异常统一收口，保留产物目录便于排查
        _finish_failure(task_id, f"{type(exc).__name__}: {exc}")
    finally:
        _running.pop(task_id, None)
        log.info("任务结束 id=%s 耗时=%.1fs", task_id, time.perf_counter() - started)


async def _invoke_engine(engine: Engine, task: models.Task, workdir: Path) -> None:
    async def emit(stage: str) -> None:
        if stage not in models.STAGES:
            return
        # 阶段上报是"尽力而为"：数据库抖动不该把已有产物的任务判为失败
        try:
            db.set_stage(task.id, stage)
            db.add_event(task.id, stage)
        except Exception:
            log.warning("阶段上报失败 id=%s stage=%s", task.id, stage, exc_info=True)

    await engine.run(task.id, task.prompt, workdir, emit)


def _finish_success(task_id: str, workdir: Path) -> None:
    """扫描产物 → 先落终态 → 再写产物元数据。

    顺序不能反：先写产物再改状态时，若用户恰好在两者之间取消，会出现
    "状态 cancelled 但 artifacts 已落库" 的不一致数据。先改状态则取消方一定赢，
    收尾直接放弃写元数据（产物目录仍保留，便于排查）。
    """
    result = scan_artifacts(workdir)
    if not db.set_status(task_id, models.STATUS_SUCCEEDED, stage="checking"):
        log.info("任务在收尾前已进入终态，跳过产物落库 id=%s", task_id)
        return
    for rel, kind, size in result.files:
        db.add_artifact(task_id, rel, kind, size)
    db.add_event(task_id, models.STATUS_SUCCEEDED)
    log.info("任务成功 id=%s 入口=%s 文件数=%d", task_id, result.entry, len(result.files))


def _finish_failure(task_id: str, message: str) -> None:
    text = (message or "unknown error").strip()[:ERROR_MAX_CHARS]
    if db.set_status(task_id, models.STATUS_FAILED, error=text):
        db.add_event(task_id, f"failed: {text}")
        log.warning("任务失败 id=%s 原因=%r", task_id, text)


async def cancel(task_id: str) -> models.Task | None:
    """用户主动取消：先落库再取消协程，避免协程收尾时把状态改回 failed。"""
    task = db.get_task(task_id)
    if task is None:
        return None
    if task.is_terminal:
        return task

    if db.set_status(task_id, models.STATUS_CANCELLED):
        db.add_event(task_id, models.STATUS_CANCELLED)
        handle = _running.get(task_id)
        if handle is not None and not handle.done():
            handle.cancel()
        log.info("任务被取消 id=%s", task_id)
    return db.get_task(task_id)


async def shutdown() -> None:
    """进程退出：取消在跑的任务，等它们收尾（会把状态记为 failed/interrupted）。"""
    handles = [h for h in _running.values() if not h.done()]
    for handle in handles:
        handle.cancel()
    if handles:
        await asyncio.gather(*handles, return_exceptions=True)
        log.info("已取消 %d 个在跑任务", len(handles))


def interrupt_stale_tasks(reason: str = "interrupted by restart") -> int:
    count = db.fail_unfinished_tasks(reason)
    if count:
        log.warning("启动清理：%d 个残留任务标记为 failed（%s）", count, reason)
    return count


# ---------- 产物扫描 ----------


@dataclass
class ScanResult:
    entry: str
    files: list[tuple[str, str, int]]  # (相对路径, kind, 字节数)


def scan_artifacts(workdir: Path) -> ScanResult:
    """扫描产物目录：跳过隐藏目录与超大文件，挑出可预览入口。"""
    if not workdir.is_dir():
        raise NoPreviewArtifact(f"产物目录不存在：{workdir}")

    files: list[tuple[str, str, int]] = []
    total = 0
    for path in sorted(workdir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(workdir)
        if any(part.startswith(".") or part in SKIP_DIRS for part in rel.parts):
            continue
        size = path.stat().st_size
        if size > settings.max_single_file_bytes:
            log.warning("跳过超大产物文件：%s (%d bytes)", rel.as_posix(), size)
            continue
        total += size
        if total > settings.max_artifact_bytes:
            raise ArtifactTooLarge(f"产物超过 {settings.max_artifact_bytes} 字节上限")
        rel_posix = rel.as_posix()
        files.append((rel_posix, models.kind_of(rel_posix), size))

    entry = pick_entry([path for path, kind, _ in files if kind == "html"])
    if entry is None:
        raise NoPreviewArtifact("产物中没有 HTML，无法预览")
    return ScanResult(entry=entry, files=files)


def pick_entry(html_paths: list[str]) -> str | None:
    """入口优先级：根目录 index.html → 路径最短的 *.html。"""
    if not html_paths:
        return None
    for candidate in ("index.html", "Index.html"):
        if candidate in html_paths:
            return candidate
    return min(html_paths, key=lambda p: (p.count("/"), len(p), p))


def entry_of(artifacts: list[models.Artifact]) -> str | None:
    return pick_entry([a.path for a in artifacts if a.kind == "html"])


def workdir_of(task_id: str) -> Path | None:
    task = db.get_task(task_id)
    return Path(task.workdir) if task else None


def resolve_artifact(task_id: str, relative: str) -> Path | None:
    """把请求路径映射到产物文件，越界一律返回 None（路径穿越防护）。"""
    root = workdir_of(task_id)
    if root is None:
        return None
    root = root.resolve()
    try:
        target = (root / relative).resolve()
    except (OSError, ValueError):
        return None
    if target != root and root not in target.parents:
        return None
    if not target.is_file():
        return None
    return target
