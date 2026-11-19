#!/usr/bin/env python
"""预置演示数据：直接写库 + 本地跑一遍 MOCK 引擎，避免现场演示干等。

为什么不走 HTTP：脚本要在服务未启动时也能用（首次部署、演示前预热），
而且写的是同一份 SQLite，服务启动后直接能在任务列表里看到。

用法：
    python scripts/seed_demo.py                  # 预置 2 条（已存在同名成功任务则跳过）
    python scripts/seed_demo.py --count 3        # 预置 3 条
    python scripts/seed_demo.py --force          # 无视已存在，强行再生成
    python scripts/seed_demo.py --dry-run        # 只打印将要预置的内容
    python scripts/seed_demo.py --prompt "做一个日历"
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import db, models  # noqa: E402
from app.config import settings  # noqa: E402
from app.engine.mock_engine import MockEngine  # noqa: E402
from app.tasks import scan_artifacts  # noqa: E402

DEMO_PROMPTS = (
    "做一个番茄钟，25 分钟专注 + 5 分钟休息，深色主题",
    "做一个待办清单，可以添加、勾选和删除，显示剩余数量",
    "做一个极简记账面板，记录收支并显示结余",
)


def new_task_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"t_{stamp}_{secrets.token_hex(2)}"


async def seed_one(prompt: str) -> str:
    """建一条 running 任务 → 跑 MOCK 引擎 → 落产物 → 置 succeeded。"""
    task_id = new_task_id()
    workdir = settings.workspace_dir / task_id
    workdir.mkdir(parents=True, exist_ok=True)

    stamp = db.now()
    db.create_task(
        models.Task(
            id=task_id,
            prompt=prompt,
            status=models.STATUS_PENDING,
            workdir=str(workdir),
            created_at=stamp,
            updated_at=stamp,
        )
    )
    db.add_event(task_id, models.STATUS_PENDING)
    db.set_status(task_id, models.STATUS_RUNNING)
    db.add_event(task_id, models.STATUS_RUNNING)

    async def emit(stage: str) -> None:
        db.set_stage(task_id, stage)
        db.add_event(task_id, stage)

    await MockEngine().run(task_id, prompt, workdir, emit)

    result = scan_artifacts(workdir)
    for rel, kind, size in result.files:
        db.add_artifact(task_id, rel, kind, size)
    db.set_status(task_id, models.STATUS_SUCCEEDED, stage="checking")
    db.add_event(task_id, models.STATUS_SUCCEEDED)
    return task_id


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="预置演示任务（直接写库，不走 HTTP）")
    parser.add_argument("--count", type=int, default=2, help="预置条数，默认 2")
    parser.add_argument("--prompt", action="append", help="自定义需求，可重复；指定后忽略内置示例")
    parser.add_argument("--force", action="store_true", help="同名成功任务已存在时也重新生成")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    prompts = list(args.prompt or DEMO_PROMPTS)[: max(1, args.count)]

    db.init_db()
    existing = {
        task.prompt
        for task in db.list_tasks(limit=200)
        if task.status == models.STATUS_SUCCEEDED
    }

    todo = [p for p in prompts if args.force or p not in existing]
    skipped = len(prompts) - len(todo)

    if args.dry_run:
        for prompt in todo:
            print(f"[plan] {prompt}")
        print(f"[plan] 跳过已存在 {skipped} 条，预置 {len(todo)} 条")
        return 0

    if not todo:
        print(f"[完成] 跳过 {skipped} 条（已存在同名成功任务），无需新增")
        return 0

    started = asyncio.get_running_loop().time()
    created: list[str] = []
    for prompt in todo:
        task_id = await seed_one(prompt)
        created.append(task_id)
        print(f"[ok] {task_id}  {prompt[:40]}")

    elapsed = asyncio.get_running_loop().time() - started
    print(
        f"[完成] 已预置 {len(created)} 条演示任务（跳过 {skipped} 条），耗时 {elapsed:.1f}s，"
        f"数据库 {settings.db_path}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
