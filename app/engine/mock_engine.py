"""MOCK 引擎：不调用任何 LLM，写入固定的可交互页面并模拟三个阶段。

用途有两个：
1. S3 阶段没有 MetaGPT 也能端到端验证「入队 → 阶段推进 → 产物扫描 → 预览」；
2. S4 之后作为降级兜底（LLM 不可用、额度不足时 `MOCK_ENGINE=1` 仍可演示）。
"""

from __future__ import annotations

import asyncio
import html
import json
from pathlib import Path

from app.engine.base import EmitFn


class MockEngine:
    name = "mock"

    async def run(self, task_id: str, prompt: str, workdir: Path, emit: EmitFn) -> None:
        workdir.mkdir(parents=True, exist_ok=True)

        await emit("planning")
        await asyncio.sleep(0.5)

        await emit("coding")
        await asyncio.sleep(1.5)
        (workdir / "index.html").write_text(_html(prompt, task_id), encoding="utf-8")
        (workdir / "styles.css").write_text(_CSS, encoding="utf-8")
        (workdir / "app.js").write_text(_JS, encoding="utf-8")
        (workdir / "meta.json").write_text(
            json.dumps(
                {"task_id": task_id, "prompt": prompt, "engine": "mock"},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        await emit("checking")
        await asyncio.sleep(0.4)


_CONTEXT_MARKER = "请基于以上对话实现最终需求："
_CONTEXT_TAIL = "。只输出单文件 HTML。"


def _requirement_of(prompt: str) -> str:
    """多轮时 prompt 是拼好的上下文，演示页只回显最后那一句需求，避免正文变成对话记录。"""
    text = (prompt or "").strip()
    if _CONTEXT_MARKER in text:
        text = text.rsplit(_CONTEXT_MARKER, 1)[1]
    return text.split(_CONTEXT_TAIL)[0].strip() or text[:200]


def _html(prompt: str, task_id: str) -> str:
    safe_prompt = html.escape(_requirement_of(prompt))
    turns = (prompt or "").count("用户：")
    note = f"引擎输入 {len(prompt or '')} 字符" + (f" · {turns} 轮上下文" if turns else "")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MOCK 生成结果</title>
<link rel="stylesheet" href="styles.css">
</head>
<body>
<main class="card">
  <span class="badge">MOCK ENGINE</span>
  <h1>这是一个可交互的演示应用</h1>
  <p class="req">{safe_prompt}</p>

  <section class="panel">
    <div class="counter">
      <button id="dec" type="button" aria-label="减一">−</button>
      <output id="value">0</output>
      <button id="inc" type="button" aria-label="加一">+</button>
    </div>
    <div class="row">
      <button id="reset" type="button" class="ghost">重置</button>
      <button id="theme" type="button" class="ghost">切换配色</button>
    </div>
  </section>

  <p class="foot">{html.escape(note)} · 本页由 MOCK 引擎写入 <code>workspace/{task_id}/</code>，S4 起由 MetaGPT 真实生成。</p>
</main>
<script src="app.js" defer></script>
</body>
</html>
"""


_CSS = """\
:root{color-scheme:dark;--bg:#0b0f14;--panel:#141a22;--line:#232d3a;--text:#e6edf3;--muted:#9ba7b4;--a0:#6366f1;--a1:#22d3ee}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;
  background:radial-gradient(900px 520px at 20% 0%,#161d27,#0b0f14 65%);
  color:var(--text);font-family:system-ui,-apple-system,"Segoe UI","PingFang SC",sans-serif}
body.light{--bg:#f6f7fb;--panel:#ffffff;--line:#dde3ec;--text:#12161d;--muted:#5b6675;color-scheme:light;
  background:radial-gradient(900px 520px at 20% 0%,#eef2ff,#f6f7fb 65%)}
.card{width:min(520px,100%);padding:24px;border:1px solid var(--line);border-radius:16px;background:var(--panel);
  box-shadow:0 30px 70px -40px rgba(0,0,0,.65)}
.badge{display:inline-block;font-size:11px;letter-spacing:.14em;color:var(--a1);border:1px solid rgba(34,211,238,.35);
  border-radius:999px;padding:2px 9px}
h1{margin:12px 0 8px;font-size:19px;line-height:1.45}
.req{margin:0 0 18px;padding:10px 12px;border-left:2px solid var(--a0);background:rgba(99,102,241,.08);
  border-radius:6px;color:var(--muted);font-size:12.5px;line-height:1.6;word-break:break-word}
.panel{border:1px solid var(--line);border-radius:12px;padding:16px}
.counter{display:flex;align-items:center;justify-content:center;gap:16px}
output{font-size:44px;font-variant-numeric:tabular-nums;min-width:96px;text-align:center;
  background:linear-gradient(135deg,var(--text),var(--a1));-webkit-background-clip:text;background-clip:text;color:transparent}
button{cursor:pointer;border-radius:10px;border:1px solid var(--line);background:transparent;color:var(--text);
  font-size:15px;padding:8px 16px;transition:transform .15s,border-color .15s}
button:hover{transform:translateY(-1px);border-color:var(--a0)}
.counter button{width:48px;height:48px;font-size:20px;line-height:1}
.row{display:flex;gap:8px;justify-content:center;margin-top:16px}
.ghost{font-size:12.5px;padding:7px 14px;color:var(--muted)}
.foot{margin:16px 0 0;font-size:11.5px;color:var(--muted);line-height:1.6}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
"""


_JS = """\
(function () {
  'use strict';
  var value = 0;
  var out = document.getElementById('value');

  function paint() {
    out.textContent = String(value);
  }

  document.getElementById('inc').addEventListener('click', function () { value += 1; paint(); });
  document.getElementById('dec').addEventListener('click', function () { value -= 1; paint(); });
  document.getElementById('reset').addEventListener('click', function () { value = 0; paint(); });
  document.getElementById('theme').addEventListener('click', function () {
    document.body.classList.toggle('light');
  });

  paint();
})();
"""
