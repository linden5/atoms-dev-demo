#!/usr/bin/env python
"""命令行单次生成：绕过 Web 与数据库，直接调用引擎层。

用途：
1. 单独验证 MetaGPT 是否真的能产出可预览的 HTML（S4 的排障入口）；
2. 在服务器上用 MOCK 跑通产物扫描，不消耗额度；
3. `--fail` 注入异常，验证失败路径的收尾与提示。

用法：
    python scripts/gen_once.py "做一个番茄钟，深色主题"
    python scripts/gen_once.py "做一个待办清单" --engine mock
    python scripts/gen_once.py "做一个记账面板" --engine metagpt --timeout 900
    python scripts/gen_once.py "验证失败路径" --fail

没有 MetaGPT 依赖时，用假包验证引擎交互（引擎代码仍是真实路径）：
    python scripts/gen_once.py "做一个计数器" --fake-metagpt success
    python scripts/gen_once.py "验证抽取失败" --fake-metagpt no_html
    python scripts/gen_once.py "验证 LLM 回退" --fake-metagpt role_broken
    python scripts/gen_once.py "验证超时" --fake-metagpt slow:3 --timeout 1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from app.config import ENGINE_MODE_AUTO, ENGINE_MODE_METAGPT, ENGINE_MODE_MOCK, settings  # noqa: E402
from app.engine.base import EmitFn  # noqa: E402
from app.tasks import scan_artifacts  # noqa: E402

ENGINE_CHOICES = (ENGINE_MODE_AUTO, ENGINE_MODE_MOCK, ENGINE_MODE_METAGPT)


class FailingEngine:
    """故障注入：用于验证"引擎抛异常 → 失败收尾"这条路径。"""

    name = "failing"

    async def run(self, task_id: str, prompt: str, workdir: Path, emit: EmitFn) -> None:
        await emit("planning")
        raise RuntimeError("故障注入：engine boom")


def build_engine(choice: str):
    if choice == ENGINE_MODE_MOCK:
        from app.engine.mock_engine import MockEngine

        return MockEngine()

    from app.engine.metagpt_engine import MetaGPTEngine, metagpt_available

    if choice == ENGINE_MODE_METAGPT:
        return MetaGPTEngine()

    available, detail = metagpt_available()
    if available:
        return MetaGPTEngine()
    print(f"[info] 未检测到 MetaGPT（{detail}），本次使用 MOCK 引擎", flush=True)
    from app.engine.mock_engine import MockEngine

    return MockEngine()


async def generate(args: argparse.Namespace) -> int:
    if args.fake_metagpt:
        import fake_metagpt

        version = fake_metagpt.install(args.fake_metagpt)
        print(f"[info] 已注入假 metagpt {version} scenario={args.fake_metagpt}", flush=True)

    if args.fail:
        engine = FailingEngine()
    else:
        try:
            engine = build_engine(args.engine)
        except RuntimeError as exc:  # 例如强制 metagpt 但环境没装：只要一条可读的提示，不要 traceback
            print(f"[失败] {exc}", file=sys.stderr)
            print("[提示] 去掉 --engine metagpt，或设 MOCK_ENGINE=1 用 MOCK 引擎验证链路", file=sys.stderr)
            return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    workdir = Path(args.out).resolve() if args.out else settings.workspace_dir / f"cli_{stamp}"
    workdir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    stages: list[str] = []

    async def emit(stage: str) -> None:
        stages.append(stage)
        print(f"[stage] {stage:<8} +{time.perf_counter() - started:5.1f}s", flush=True)

    print(f"[info] engine={engine.name} timeout={args.timeout}s workdir={workdir}", flush=True)
    try:
        await asyncio.wait_for(engine.run(f"cli_{stamp}", args.prompt, workdir, emit), timeout=args.timeout)
        result = scan_artifacts(workdir)
    except asyncio.TimeoutError:
        print(f"[失败] 超时 {args.timeout}s", file=sys.stderr)
        return 1
    except Exception as exc:  # 与编排层一致：异常信息透出，产物目录保留
        print(f"[失败] {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"[info] 产物目录保留在 {workdir}", file=sys.stderr)
        return 1

    elapsed = time.perf_counter() - started
    print(f"[完成] 入口={result.entry} 文件数={len(result.files)} 耗时={elapsed:.1f}s", flush=True)
    for rel, kind, size in result.files:
        print(f"  - {rel:<24} {kind:<6} {size:>8} B")
    print(f"[info] 预览命令：python -m uvicorn app.main:app --port 8000 后访问 /api/tasks/<task_id>/preview")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="命令行单次生成（不经 Web 与数据库）")
    parser.add_argument("prompt", help="自然语言需求")
    parser.add_argument("--engine", choices=ENGINE_CHOICES, default=ENGINE_MODE_AUTO, help="引擎选择，默认 auto")
    parser.add_argument("--out", help="产物目录，默认 workspace/cli_<时间戳>")
    parser.add_argument("--timeout", type=int, default=settings.task_timeout, help="超时秒数，默认同 TASK_TIMEOUT")
    parser.add_argument("--fail", action="store_true", help="故障注入：让引擎直接抛异常，用于验证失败路径")
    parser.add_argument(
        "--fake-metagpt",
        metavar="SCENARIO",
        help="注入假 metagpt（success / no_html / role_broken / slow:3），无需真实依赖即可验证引擎交互",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.prompt.strip():
        print("prompt 不能为空", file=sys.stderr)
        return 2
    return asyncio.run(generate(args))


if __name__ == "__main__":
    raise SystemExit(main())
