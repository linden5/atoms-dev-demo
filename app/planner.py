"""多轮对话：澄清判断与上下文组装。

设计取舍：
1. 默认走**规则**判断，不额外消耗一次 LLM 调用 —— 演示场景下延迟、费用、行为都可预测；
2. 只拦一次：已经追问过就不再拦，避免"反复追问把用户挡在门外"；
3. 判断不了就放行生成，最坏情况是"需求不够具体也生成了"，不会卡死；
4. 历史拼进 prompt 时有长度上限，防止多轮把 token 顶爆。

留给后续的口子见 docs/design.md 4.7：引擎若实现 `clarify(history)`，可换成 LLM 澄清。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 少于这个字数基本无法判断要做什么
MIN_REQUIREMENT_CHARS = 12
# 拼进引擎 prompt 的历史上限（字符）
HISTORY_MAX_CHARS = 1200

_VAGUE = re.compile(
    r"^[\s，。,.!！]*(?:帮我|给我|请)?[\s]*(?:做|写|生成|来|搞)(?:一?个|一?款)?[\s]*"
    r"(?:网页|网站|页面|应用|app|小程序|工具|demo|东西|系统)[\s，。,.!！]*$",
    re.IGNORECASE,
)

FIRST_QUESTION = (
    "要生成得更准，先确认两件事：\n"
    "1）这个应用的核心功能是什么（比如计时、增删条目、记录收支）？\n"
    "2）有指定的界面风格或配色吗？没有的话我按深色简洁风来做。"
)

VAGUE_QUESTION = (
    "可以，不过我还需要知道具体做什么。"
    "比如「番茄钟」「待办清单」「记账面板」——你想做哪一类？"
)

SHORT_QUESTION = (
    "信息还不太够，能再补充一点吗？"
    "比如：给谁用、要解决什么问题、需要哪些按钮或操作。"
)


@dataclass(frozen=True)
class Decision:
    """ask=True 表示先追问、不建任务。"""

    ask: bool
    question: str = ""

    def to_dict(self) -> dict:
        return {"ask": self.ask, "question": self.question}


def _content(item: dict) -> str:
    return str(item.get("content") or "").strip()


def already_asked(history: list[dict]) -> bool:
    return any(
        item.get("role") == "assistant" and item.get("kind") == "question" for item in history
    )


def user_text(history: list[dict]) -> str:
    return " ".join(_content(item) for item in history if item.get("role") == "user").strip()


def decide(history: list[dict]) -> Decision:
    """根据会话历史判断这一次是该追问还是直接生成。"""
    if already_asked(history):
        return Decision(ask=False)

    text = user_text(history)
    if not text:
        return Decision(ask=True, question=FIRST_QUESTION)
    if _VAGUE.match(text):
        return Decision(ask=True, question=VAGUE_QUESTION)
    if len(text) < MIN_REQUIREMENT_CHARS:
        return Decision(ask=True, question=SHORT_QUESTION)
    return Decision(ask=False)


def build_prompt(history: list[dict], requirement: str | None = None) -> str:
    """把对话历史压成一段文本交给单角色引擎；产物卡片不进上下文。"""
    lines: list[str] = []
    for item in history:
        if item.get("kind") == "artifact":
            continue
        content = _content(item)
        if not content:
            continue
        who = "用户" if item.get("role") == "user" else "助手"
        lines.append(f"{who}：{content}")

    body = "\n".join(lines)
    if len(body) > HISTORY_MAX_CHARS:
        body = "……（更早的对话已省略）\n" + body[-HISTORY_MAX_CHARS:]

    target = (requirement or "").strip()
    tail = f"请基于以上对话实现最终需求：{target}" if target else "请基于以上对话实现最终需求"
    return f"下面是用户与你的对话记录（按时间顺序）。\n\n{body}\n\n{tail}。只输出单文件 HTML。"
