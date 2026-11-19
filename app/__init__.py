"""Atoms Demo 后端包。

硬性约束：本包（含子模块）顶层不得 `import metagpt`。
引擎实现只有在 `app/engine/metagpt_engine.py` 内部才允许懒加载 MetaGPT，
这样未安装 MetaGPT 的环境（S2 / S3、MOCK 模式）也能正常启动服务。
"""

from __future__ import annotations

__version__ = "1.0.0"

# 当前交付步骤，用于 /health 与前端顶栏展示
STEP = "s8"
