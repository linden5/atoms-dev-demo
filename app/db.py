"""SQLite 访问层。

为什么只用一个连接：uvicorn 单 worker 下所有请求与后台任务都在同一个事件循环线程里，
共用一条连接既避免并发写锁，也避免每次请求重新打开文件。所有函数都是同步的（毫秒级），
不放进线程池，保持代码简单。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from app import models
from app.config import settings

log = logging.getLogger("atoms.db")

_conn: sqlite3.Connection | None = None


def now() -> str:
    """毫秒精度：阶段耗时由 events 时间戳推导，只到秒会让每段都显示 0.0s。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(settings.db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def init_db() -> None:
    conn = connect()
    with conn:
        for statement in models.SCHEMA_STATEMENTS:
            conn.execute(statement)
        _migrate(conn)
    log.info("数据库就绪 · %s", settings.db_path)


def _migrate(conn: sqlite3.Connection) -> None:
    """只增不改：历史库缺列时补列，绝不改类型或删列。

    S3 建表时已包含 parent_id，这里保留兜底，保证从更早的库升级也能跑。
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "parent_id" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN parent_id TEXT")


# ---------- 任务 ----------


def create_task(task: models.Task) -> None:
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO tasks (id, parent_id, prompt, status, stage, error, workdir, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.parent_id,
                task.prompt,
                task.status,
                task.stage,
                task.error,
                task.workdir,
                task.created_at,
                task.updated_at,
            ),
        )


def _hydrate(row: sqlite3.Row, *, with_events: bool) -> models.Task:
    task = models.Task.from_row(row)
    task.artifacts = list_artifacts(task.id)
    if with_events:
        task.events = list_events(task.id)
    return task


def get_task(task_id: str, *, with_events: bool = False) -> models.Task | None:
    row = connect().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    return _hydrate(row, with_events=with_events)


def list_tasks(limit: int = 50, offset: int = 0) -> list[models.Task]:
    rows = connect().execute(
        "SELECT * FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [_hydrate(row, with_events=False) for row in rows]


def count_tasks() -> int:
    return int(connect().execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"])


def set_status(
    task_id: str,
    status: str,
    *,
    stage: str | None = None,
    error: str | None = None,
) -> bool:
    """写状态；终态不可回退，已处于终态时返回 False。"""
    conn = connect()
    with conn:
        cursor = conn.execute(
            """
            UPDATE tasks
               SET status = ?, stage = COALESCE(?, stage), error = ?, updated_at = ?
             WHERE id = ? AND status NOT IN (?, ?, ?)
            """,
            (
                status,
                stage,
                error,
                now(),
                task_id,
                models.STATUS_SUCCEEDED,
                models.STATUS_FAILED,
                models.STATUS_CANCELLED,
            ),
        )
    return cursor.rowcount > 0


def set_stage(task_id: str, stage: str) -> None:
    conn = connect()
    with conn:
        conn.execute(
            "UPDATE tasks SET stage = ?, updated_at = ? WHERE id = ? AND status = ?",
            (stage, now(), task_id, models.STATUS_RUNNING),
        )


def delete_task(task_id: str) -> None:
    conn = connect()
    with conn:
        conn.execute("DELETE FROM artifacts WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM task_events WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


def fail_unfinished_tasks(reason: str) -> int:
    """启动钩子：把上一个进程残留的 pending/running 任务收尾，避免永久挂起。"""
    conn = connect()
    with conn:
        cursor = conn.execute(
            """
            UPDATE tasks
               SET status = ?, error = ?, updated_at = ?
             WHERE status IN (?, ?)
            """,
            (models.STATUS_FAILED, reason, now(), models.STATUS_PENDING, models.STATUS_RUNNING),
        )
    return cursor.rowcount


# ---------- 产物 ----------


def add_artifact(task_id: str, path: str, kind: str, size: int) -> None:
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO artifacts (task_id, path, kind, size, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, path, kind, size, now()),
        )


def list_artifacts(task_id: str) -> list[models.Artifact]:
    rows = connect().execute(
        "SELECT path, kind, size FROM artifacts WHERE task_id = ? ORDER BY path",
        (task_id,),
    ).fetchall()
    return [models.Artifact(path=row["path"], kind=row["kind"], size=row["size"]) for row in rows]


# ---------- 版本链 ----------


def root_of(task_id: str) -> str:
    """沿 parent_id 向上找到版本链的根；异常（环、断链）时返回找到的最后一个已知节点。"""
    conn = connect()
    current = task_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        row = conn.execute("SELECT id, parent_id FROM tasks WHERE id = ?", (current,)).fetchone()
        if row is None:
            break
        parent = row["parent_id"]
        if not parent:
            return row["id"]
        current = parent
    return current or task_id


def list_versions(root_id: str, limit: int = 200) -> list[models.Task]:
    """从根节点广度优先收集同一版本树，并按创建时间升序编号（v1 最早）。

    一次查询取回候选任务后在内存里建父子关系：任务量级是演示级别（几十条），
    比按层递归查库更简单，也避免 N+1。
    """
    rows = connect().execute(
        "SELECT * FROM tasks ORDER BY created_at ASC, rowid ASC LIMIT ?", (limit,)
    ).fetchall()
    tasks = {row["id"]: _hydrate(row, with_events=False) for row in rows}
    if root_id not in tasks:
        return []

    children: dict[str, list[str]] = {}
    for task in tasks.values():
        if task.parent_id and task.parent_id in tasks:
            children.setdefault(task.parent_id, []).append(task.id)

    order: list[str] = []
    queue = [root_id]
    while queue:
        current = queue.pop(0)
        if current in order:
            continue
        order.append(current)
        queue.extend(children.get(current, []))

    versions = [tasks[task_id] for task_id in order if task_id in tasks]
    versions.sort(key=lambda t: (t.created_at, t.id))
    return versions


# ---------- 事件 ----------


def add_event(task_id: str, message: str) -> None:
    conn = connect()
    with conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS s FROM task_events WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        conn.execute(
            "INSERT INTO task_events (task_id, seq, message, created_at) VALUES (?, ?, ?, ?)",
            (task_id, int(row["s"]) + 1, message, now()),
        )


def list_events(task_id: str) -> list[dict]:
    rows = connect().execute(
        "SELECT seq, message, created_at FROM task_events WHERE task_id = ? ORDER BY seq",
        (task_id,),
    ).fetchall()
    return [{"seq": row["seq"], "message": row["message"], "created_at": row["created_at"]} for row in rows]
