"""表结构与数据模型。

约定：schema 只增不改（见 AGENTS.md）。新增字段一律走 `db._migrate()` 里的
`ALTER TABLE ... ADD COLUMN`，禁止改列类型或删列，保证任意历史 tag 都能启动。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

# ---------- 状态机 ----------

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED})

STAGES = ("planning", "coding", "checking")

ARTIFACT_KINDS = {
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".js": "js",
    ".mjs": "js",
    ".json": "json",
    ".svg": "image",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".ico": "image",
}

# ---------- DDL ----------

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS tasks (
      id          TEXT PRIMARY KEY,
      parent_id   TEXT,
      prompt      TEXT NOT NULL,
      status      TEXT NOT NULL,
      stage       TEXT,
      error       TEXT,
      workdir     TEXT NOT NULL,
      created_at  TEXT NOT NULL,
      updated_at  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS artifacts (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id    TEXT NOT NULL,
      path       TEXT NOT NULL,
      kind       TEXT NOT NULL,
      size       INTEGER NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id)",
    """
    CREATE TABLE IF NOT EXISTS task_events (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id    TEXT NOT NULL,
      seq        INTEGER NOT NULL,
      message    TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id, seq)",
    # ---- 多轮对话：只新增表，不动 tasks / artifacts / task_events ----
    """
    CREATE TABLE IF NOT EXISTS sessions (
      id          TEXT PRIMARY KEY,
      title       TEXT NOT NULL,
      created_at  TEXT NOT NULL,
      updated_at  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS messages (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id  TEXT NOT NULL,
      role        TEXT NOT NULL,
      kind        TEXT NOT NULL DEFAULT 'text',
      content     TEXT NOT NULL,
      task_id     TEXT,
      created_at  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)",
)


# ---------- 数据类 ----------


@dataclass
class Artifact:
    path: str
    kind: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "size": self.size}


@dataclass
class Task:
    id: str
    prompt: str
    status: str
    workdir: str
    created_at: str
    updated_at: str
    parent_id: str | None = None
    stage: str | None = None
    error: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Task":
        return cls(
            id=row["id"],
            prompt=row["prompt"],
            status=row["status"],
            workdir=row["workdir"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            parent_id=row["parent_id"],
            stage=row["stage"],
            error=row["error"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "status": self.status,
            "stage": self.stage,
            "parent_id": self.parent_id,
            "error": self.error,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "events": self.events,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def summary(self) -> dict[str, Any]:
        """列表接口用的精简形态，不带事件与产物明细。"""
        return {
            "id": self.id,
            "prompt": self.prompt,
            "status": self.status,
            "stage": self.stage,
            "parent_id": self.parent_id,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "artifact_count": len(self.artifacts),
        }


# ---------- 多轮对话 ----------

MESSAGE_ROLE_USER = "user"
MESSAGE_ROLE_ASSISTANT = "assistant"

# text=普通气泡，question=Agent 追问，artifact=生成结果，error=失败提示
MESSAGE_KINDS = ("text", "question", "artifact", "error")


@dataclass
class Message:
    id: int
    session_id: str
    role: str
    kind: str
    content: str
    created_at: str
    task_id: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Message":
        return cls(
            id=int(row["id"]),
            session_id=row["session_id"],
            role=row["role"],
            kind=row["kind"],
            content=row["content"],
            created_at=row["created_at"],
            task_id=row["task_id"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "kind": self.kind,
            "content": self.content,
            "task_id": self.task_id,
            "created_at": self.created_at,
        }


@dataclass
class Session:
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0
    last_task_id: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Session":
        return cls(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
            "last_task_id": self.last_task_id,
        }


def kind_of(path: str) -> str:
    from pathlib import PurePosixPath

    return ARTIFACT_KINDS.get(PurePosixPath(path).suffix.lower(), "other")
