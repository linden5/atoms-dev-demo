"""真实生成引擎：懒加载 MetaGPT，单角色完成"需求 → 单文件网页应用"。

为什么要懒加载：MetaGPT 依赖很重（faiss / pandas / 老版本 aiohttp），只能在 Python 3.11 上装。
`app/` 其它模块不得导入本模块的 `metagpt`，否则没装依赖的环境连 S2/S3 都起不来。
本文件内部也只在函数里 `import metagpt`，模块导入本身是零成本的。

契约与 MOCK 引擎完全一致（见 `app/engine/base.py`）：只往 workdir 写文件、不改数据库、
阶段上报尽力而为、产物结构交给 `scan_artifacts()` 解释。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from app.engine.base import EmitFn

log = logging.getLogger("atoms.engine.metagpt")

# 生成约束：单文件、无外联、深色响应式。刻意压低自由度，减少"多文件 + CDN"导致的预览失败
PROMPT = """你是资深前端工程师。请把下面的需求实现成一个**单文件**网页应用。

硬性要求：
1. 只输出一个完整的 HTML 文档，CSS 与 JS 全部内联，不要输出任何解释文字；
2. 不引用任何外部资源（CDN、字体、图片链接），需要图形时用内联 SVG；
3. 中文界面，响应式，375px 宽度下仍可用；
4. 深色主题、细边框、单一强调色，交互反馈控制在 150–200ms；
5. 打开即可用，不依赖网络与后端。

需求：{requirement}
"""

_FENCE = re.compile(r"```(?:html|HTML)?\s*(.*?)```", re.S)
_DOCTYPE = re.compile(r"<!doctype\s+html", re.I)
_HTML_TAG = re.compile(r"<html[\s>]", re.I)
_CLOSING = re.compile(r"</html\s*>", re.I)


def metagpt_available() -> tuple[bool, str]:
    """探测 MetaGPT 是否可导入；只在函数内 import，保证模块本身可被安全导入。"""
    try:
        import metagpt  # noqa: F401  # 延迟导入，顶层不 import 是硬性约束
    except Exception as exc:  # ImportError 之外还可能是依赖版本冲突
        return False, f"{type(exc).__name__}: {exc}"
    return True, getattr(metagpt, "__version__", "unknown")


def extract_html(text: str) -> str | None:
    """从模型输出里取出 HTML：优先代码块，其次全文，兜底从第一个 <!doctype>/<html 起截断。"""
    if not text or not text.strip():
        return None
    fenced = _FENCE.search(text)
    candidate = fenced.group(1) if fenced else text

    starts = [m.start() for m in (_DOCTYPE.search(candidate), _HTML_TAG.search(candidate)) if m]
    if not starts:
        return None
    begin = min(starts)
    ends = [m.end() for m in _CLOSING.finditer(candidate)]
    body = candidate[begin : ends[-1]] if ends else candidate[begin:]
    body = body.strip()
    return body or None


_role_cls: Any = None


def _build_role() -> Any:
    """构造单角色（非 Team）。类在第一次调用时才定义，避免模块导入即依赖 metagpt。"""
    global _role_cls
    if _role_cls is not None:
        return _role_cls

    from metagpt.actions import Action
    from metagpt.roles import Role

    class BuildPage(Action):
        name: str = "BuildPage"

        async def run(self, requirement: str) -> str:  # type: ignore[override]
            return await self._aask(PROMPT.format(requirement=requirement))

    class Builder(Role):
        name: str = "Builder"
        profile: str = "Web App Builder"
        goal: str = "把自然语言需求交付成一个打开即用的单文件网页应用"
        constraints: str = "只输出 HTML；不引用外部资源；单文件内联 CSS/JS"

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.set_actions([BuildPage])

    _role_cls = Builder
    return _role_cls


class MetaGPTEngine:
    """单角色生成。

    优先走 Role（与 design 的"单角色"一致）；若 MetaGPT 版本差异导致
    Role/Action API 不兼容，退回直接调用其 LLM 层，保证引擎整体仍可用。
    """

    name = "metagpt"

    def __init__(self) -> None:
        available, detail = metagpt_available()
        if not available:
            raise RuntimeError(
                "MetaGPT 不可用（需要 Python 3.11 + metagpt）: "
                f"{detail}；可改用 MOCK_ENGINE=1 或留空走 auto"
            )
        self.version = detail

    async def run(self, task_id: str, prompt: str, workdir: Path, emit: EmitFn) -> None:
        workdir.mkdir(parents=True, exist_ok=True)

        await emit("planning")
        await emit("coding")

        started = asyncio.get_running_loop().time()
        raw, mode = await self._generate(prompt)
        html = extract_html(raw)
        if html is None:
            raise ValueError(f"模型输出里没有可解析的 HTML（长度 {len(raw or '')}）")

        (workdir / "index.html").write_text(html, encoding="utf-8")
        (workdir / "meta.json").write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "prompt": prompt,
                    "engine": "metagpt",
                    "engine_version": self.version,
                    "mode": mode,
                    "raw_chars": len(raw or ""),
                    "elapsed_s": round(asyncio.get_running_loop().time() - started, 1),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        log.info(
            "MetaGPT 生成完成 task=%s mode=%s chars=%d elapsed=%.1fs",
            task_id,
            mode,
            len(html),
            asyncio.get_running_loop().time() - started,
        )

        await emit("checking")

    async def _generate(self, requirement: str) -> tuple[str, str]:
        try:
            role_cls = _build_role()
            from metagpt.schema import Message

            role = role_cls()
            result = await role.run(Message(content=requirement, role="user"))
            return _content_of(result), "role"
        except (ImportError, AttributeError, TypeError) as exc:
            log.warning("Role 路径不可用，退回 LLM 直调：%s", exc)
            from metagpt.llm import LLM

            llm = LLM()
            return await llm.aask(PROMPT.format(requirement=requirement)), "llm"


def _content_of(result: Any) -> str:
    """Role.run 的返回值在 0.8.x 是 Message，也可能是 str，统一取出正文。"""
    if result is None:
        return ""
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    return result if isinstance(result, str) else str(result)
