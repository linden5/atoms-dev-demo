"""引擎契约。

三条硬约定（对应 docs/design.md 4.3）：
1. 引擎只把文件写进 `workdir`，不改数据库、不返回 HTML 字符串；
2. 必须可被取消：内部用 asyncio，且不吞 `CancelledError`；
3. 阶段上报是"尽力而为"，缺失不影响任务成功判定。
"""

from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Protocol, runtime_checkable

# 上报阶段：planning / coding / checking
EmitFn = Callable[[str], Awaitable[None]]


@runtime_checkable
class Engine(Protocol):
    name: str

    async def run(
        self,
        task_id: str,
        prompt: str,
        workdir: Path,
        emit: EmitFn,
        *,
        parent_html: str | None = None,
        requirement: str | None = None,
    ) -> None:
        """生成产物到 workdir。抛异常即视为失败，由编排层统一收尾。

        `parent_html` 非空表示迭代修复（父版本产物存在）：LLM 引擎改走
        REVISION_PROMPT 分支，其他引擎可忽略这两个参数。
        """
        ...
