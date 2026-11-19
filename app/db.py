"""SQLite 访问层。

为什么只用一个连接：uvicorn 单 worker 下所有请求与后台任务都在同一个事件循环线程里，
共用一条连接既避免并发写锁，也避免每次请求重新打开文件。所有函数都是同步的（毫秒级），
不放进线程池，保持代码简单。
"""

from __future__ import annotations

import logging
import secrets
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
    # S8：版本名（前端版本卡片显示"版本 2：绿色主题"而不是只有 v2）
    if "version_title" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN version_title TEXT")


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


def set_version_title(task_id: str, title: str) -> None:
    """写版本名（"版本 2：绿色主题"里的冒号后半段），失败不影响任务结果。"""
    conn = connect()
    with conn:
        conn.execute(
            "UPDATE tasks SET version_title = ?, updated_at = ? WHERE id = ?",
            (title[:40], now(), task_id),
        )


def delete_task(task_id: str) -> None:
    conn = connect()
    with conn:
        conn.execute("DELETE FROM artifacts WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM task_events WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM task_data WHERE task_id = ?", (task_id,))
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


# ---------- 应用存储（S9） ----------
#
# 预览 iframe 是 opaque origin（sandbox 无 allow-same-origin），localStorage 访问会抛
# SecurityError，生成应用的数据改由平台代管（表 task_data）。
# 存储按版本链根任务归档：同一应用迭代出的新版本共享同一份数据，与发布的
# Update 语义（地址指向新版本）对齐。


def ensure_task_data(task_id: str) -> str:
    """确保任务（实际是其版本链根任务）有应用存储行，返回访问 token。

    懒创建：历史任务没有行也能在首次预览/访问时补上，老数据不受影响。
    """
    root_id = root_of(task_id)
    conn = connect()
    with conn:
        row = conn.execute(
            "SELECT token FROM task_data WHERE task_id = ?", (root_id,)
        ).fetchone()
        if row is not None:
            return str(row["token"])
        token = secrets.token_hex(16)
        conn.execute(
            "INSERT OR IGNORE INTO task_data (task_id, token, data, updated_at) VALUES (?, ?, '{}', ?)",
            (root_id, token, now()),
        )
        row = conn.execute(
            "SELECT token FROM task_data WHERE task_id = ?", (root_id,)
        ).fetchone()
    return str(row["token"])


def data_token_matches(task_id: str, token: str) -> bool:
    """校验访问令牌；opaque origin 不带 cookie，令牌只能放在 URL 上。"""
    root_id = root_of(task_id)
    row = connect().execute(
        "SELECT token FROM task_data WHERE task_id = ?", (root_id,)
    ).fetchone()
    if row is None or not token:
        return False
    return secrets.compare_digest(str(row["token"]), token)


def get_task_data_raw(task_id: str) -> str | None:
    root_id = root_of(task_id)
    row = connect().execute(
        "SELECT data FROM task_data WHERE task_id = ?", (root_id,)
    ).fetchone()
    return row["data"] if row else None


def set_task_data_raw(task_id: str, text: str) -> None:
    """整包覆盖写入；行不存在时先补建（token 随之生成）。"""
    token = ensure_task_data(task_id)
    root_id = root_of(task_id)
    conn = connect()
    with conn:
        conn.execute(
            "UPDATE task_data SET data = ?, updated_at = ? WHERE task_id = ? AND token = ?",
            (text, now(), root_id, token),
        )


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


# ---------- 会话与消息 ----------


def new_session_id() -> str:
    return "s_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(2)


def create_session(title: str = "") -> models.Session:
    conn = connect()
    stamp = now()
    session_id = new_session_id()
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title.strip()[:60], stamp, stamp),
        )
    return models.Session(id=session_id, title=title.strip()[:60], created_at=stamp, updated_at=stamp)


def get_session(session_id: str) -> models.Session | None:
    conn = connect()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    session = models.Session.from_row(row)
    session.message_count = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()["c"]
    )
    last = conn.execute(
        """
        SELECT task_id FROM messages
         WHERE session_id = ? AND task_id IS NOT NULL
         ORDER BY id DESC LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    session.last_task_id = last["task_id"] if last else None
    return session


def list_sessions(limit: int = 50, offset: int = 0) -> list[models.Session]:
    """列表带消息数与最后一个任务，避免前端再逐条查询。"""
    rows = connect().execute(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
               (SELECT m.task_id FROM messages m
                 WHERE m.session_id = s.id AND m.task_id IS NOT NULL
                 ORDER BY m.id DESC LIMIT 1) AS last_task_id
          FROM sessions s
         ORDER BY s.updated_at DESC, s.rowid DESC
         LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    sessions = []
    for row in rows:
        session = models.Session.from_row(row)
        session.message_count = int(row["message_count"])
        session.last_task_id = row["last_task_id"]
        sessions.append(session)
    return sessions


def count_sessions() -> int:
    return int(connect().execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"])


def add_message(
    session_id: str,
    role: str,
    kind: str,
    content: str,
    task_id: str | None = None,
) -> models.Message:
    """写一条消息，同时推进会话时间；首个用户消息顺带补会话标题。"""
    conn = connect()
    stamp = now()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO messages (session_id, role, kind, content, task_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, role, kind, content, task_id, stamp),
        )
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (stamp, session_id))
        if role == models.MESSAGE_ROLE_USER:
            title = " ".join(content.split())[:24]
            if title:
                conn.execute(
                    "UPDATE sessions SET title = ? WHERE id = ? AND title = ''",
                    (title, session_id),
                )
        elif kind == "artifact":
            # 首条消息常是"做个网页"这种模糊话（正因为它才会触发追问），
            # 等真正生成了再用这句需求补一个能区分会话的标题
            title = " ".join(content.split())[:24]
            if title:
                conn.execute(
                    "UPDATE sessions SET title = ? WHERE id = ? AND LENGTH(title) < ?",
                    (title, session_id, 12),
                )
    return models.Message(
        id=int(cursor.lastrowid),
        session_id=session_id,
        role=role,
        kind=kind,
        content=content,
        created_at=stamp,
        task_id=task_id,
    )


def list_messages(session_id: str, limit: int = 200) -> list[models.Message]:
    rows = connect().execute(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [models.Message.from_row(row) for row in rows]


def last_generated_task(session_id: str) -> str | None:
    """会话里最近一次成功产出的任务，作为"继续修改"的父版本。

    S8 修正：必须连 tasks 过滤 status，否则上一轮生成失败时，下一次修改会把
    失败版本当成父版本（preview 为空、版本链断在坏节点上）。
    """
    row = connect().execute(
        """
        SELECT m.task_id FROM messages m
          JOIN tasks t ON t.id = m.task_id
         WHERE m.session_id = ? AND m.kind = ? AND m.task_id IS NOT NULL
           AND t.status = ?
         ORDER BY m.id DESC LIMIT 1
        """,
        (session_id, "artifact", models.STATUS_SUCCEEDED),
    ).fetchone()
    return row["task_id"] if row else None


def session_of_task(task_id: str) -> str | None:
    """反查任务属于哪个会话；发布功能用它判断"同一个应用的迭代"。"""
    row = connect().execute(
        "SELECT session_id FROM messages WHERE task_id = ? LIMIT 1", (task_id,)
    ).fetchone()
    return row["session_id"] if row else None


# ---------- 发布 ----------


def create_publish(publish: models.Publish) -> None:
    """插入公开地址；slug 冲突会抛 sqlite3.IntegrityError，由接口层转成 409。"""
    conn = connect()
    with conn:
        conn.execute(
            """
            INSERT INTO publishes (slug, task_id, session_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                publish.slug,
                publish.task_id,
                publish.session_id,
                publish.title,
                publish.created_at,
                publish.updated_at,
            ),
        )


def get_publish(slug: str) -> models.Publish | None:
    row = connect().execute("SELECT * FROM publishes WHERE slug = ?", (slug,)).fetchone()
    return models.Publish.from_row(row) if row else None


def get_publish_by_task(task_id: str) -> models.Publish | None:
    row = connect().execute(
        "SELECT * FROM publishes WHERE task_id = ? ORDER BY updated_at DESC LIMIT 1", (task_id,)
    ).fetchone()
    return models.Publish.from_row(row) if row else None


def get_publish_by_session(session_id: str) -> models.Publish | None:
    """一个会话（= 一个应用）只取最近更新的那条公开地址。"""
    row = connect().execute(
        "SELECT * FROM publishes WHERE session_id = ? ORDER BY updated_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return models.Publish.from_row(row) if row else None


def list_publishes(limit: int = 50) -> list[models.Publish]:
    rows = connect().execute(
        "SELECT * FROM publishes ORDER BY updated_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [models.Publish.from_row(row) for row in rows]


def update_publish_target(slug: str, task_id: str, title: str) -> bool:
    """把公开地址指向新版本（Atoms 的 Update 语义），返回是否命中。"""
    conn = connect()
    with conn:
        cursor = conn.execute(
            "UPDATE publishes SET task_id = ?, title = ?, updated_at = ? WHERE slug = ?",
            (task_id, title, now(), slug),
        )
    return cursor.rowcount > 0


def delete_publish(slug: str) -> bool:
    conn = connect()
    with conn:
        cursor = conn.execute("DELETE FROM publishes WHERE slug = ?", (slug,))
    return cursor.rowcount > 0
