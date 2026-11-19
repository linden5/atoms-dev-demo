#!/usr/bin/env python
"""用「假 MetaGPT」启动服务：不需要 Python 3.11 / metagpt / API Key。

作用是把 `app/engine/metagpt_engine.py` 的完整链路放进 Web 里跑一遍：
顶栏 chip 显示 `引擎 METAGPT`，三阶段推进、产物落盘、失败信息展示都是真实代码，
只有"模型返回的那段文本"来自 `scripts/fake_metagpt.py`。

用法：
    python scripts/serve_fake_metagpt.py
    python scripts/serve_fake_metagpt.py --scenario no_html          # 看失败态与错误提示
    python scripts/serve_fake_metagpt.py --scenario slow:6 --port 8001  # 看生成中/取消

注意：这是本地验证工具，不是部署方式；线上仍走 scripts/deploy.sh + systemd。
若环境里设了 `MOCK_ENGINE=1`，会强制走 MOCK 引擎，假 metagpt 不会生效。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for candidate in (ROOT, ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


def main() -> int:
    parser = argparse.ArgumentParser(description="以假 MetaGPT 启动服务（本地验证用）")
    parser.add_argument(
        "--scenario",
        default="success",
        help="success / no_html / role_broken / slow:6",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import fake_metagpt
    import uvicorn

    version = fake_metagpt.install(args.scenario)
    print(f"[info] 假 metagpt {version} 已注入，scenario={args.scenario}", flush=True)
    print(f"[info] 打开 http://{args.host}:{args.port}/ ，顶栏应显示「引擎 METAGPT」", flush=True)

    uvicorn.run("app.main:app", host=args.host, port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
