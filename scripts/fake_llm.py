#!/usr/bin/env python
"""假的 OpenAI 兼容端点：没有 Key 也能验证 `app/engine/llm_engine.py` 的真实代码路径。

为什么需要它：LLM 引擎的行为（重试、体检、自检修正、超时、鉴权错误提示）
不应该只在"有额度的那台机器上"才被验证。这里用一个 stdlib 的 HTTP 服务
伪造 `/v1/chat/completions`，按提示词内容区分「规划 / 编码 / 审查 / 修正」四类请求。

用法：
    python scripts/fake_llm.py --port 8099 --scenario success
    # 另开一个终端：
    LLM_BASE_URL=http://127.0.0.1:8099/v1 LLM_MODEL=fake LLM_API_KEY=dummy \\
      python scripts/gen_once.py "做一个待办清单" --engine llm

场景：
    success   规划 + 合法 HTML，审查通过（默认）
    retry     第一次编码返回残页，第二次合法（验证重试）
    issues    审查报问题，修正稿合法且带 [fixed] 标记（验证自检修正）
    no_html   两次编码都不含 HTML（验证人话报错）
    slow:N    每次响应前睡 N 秒（配 --timeout 1 验证超时）
    error:500 返回 500（验证重试 + 错误映射）
    error:401 返回 401（验证鉴权错误不重试，直接给人话）
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCENARIOS = ("success", "retry", "issues", "no_html", "slow:2", "error:500", "error:401")


class FakeHTTPError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"fake http {code}")
        self.code = code


def _page(requirement: str, tag: str = "") -> str:
    """一个真的能点的单文件应用：加任务 / 删任务 / 合计。"""
    safe = requirement.replace("<", "&lt;").replace(">", "&gt;")[:80]
    marker = f"<p class=\"tag\" data-tag=\"{tag}\">{tag}</p>" if tag else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>任务清单</title>
<style>
:root{{color-scheme:dark;--bg:#0b0f14;--panel:#141a22;--line:#232d3a;--text:#e6edf3;--muted:#9ba7b4;--accent:#22d3ee}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;padding:24px;background:var(--bg);color:var(--text);
font-family:system-ui,-apple-system,"Segoe UI","PingFang SC",sans-serif;display:grid;place-items:start center}}
.app{{width:min(560px,100%);background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px}}
h1{{font-size:18px;margin:0 0 6px}}
.req{{margin:0 0 16px;color:var(--muted);font-size:12.5px;line-height:1.6;word-break:break-word}}
form{{display:flex;gap:8px;margin-bottom:14px}}
input{{flex:1 1 auto;min-width:0;padding:10px 12px;border-radius:10px;border:1px solid var(--line);
background:transparent;color:var(--text);font-size:14px}}
button{{cursor:pointer;border-radius:10px;border:1px solid var(--line);background:transparent;
color:var(--text);padding:9px 14px;font-size:14px;transition:border-color .15s,transform .15s}}
button:hover{{border-color:var(--accent);transform:translateY(-1px)}}
ul{{list-style:none;margin:0;padding:0;display:grid;gap:8px}}
li{{display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--line);border-radius:10px}}
li span{{flex:1 1 auto;word-break:break-word}}
li.done span{{color:var(--muted);text-decoration:line-through}}
.foot{{margin-top:14px;display:flex;justify-content:space-between;color:var(--muted);font-size:12.5px}}
.tag{{margin:0;color:var(--accent);font-size:11px;letter-spacing:.1em}}
.empty{{color:var(--muted);font-size:13px;padding:14px 2px}}
</style>
</head>
<body>
<main class="app">
  <h1>任务清单</h1>
  <p class="req">需求：{safe}</p>
  {marker}
  <form id="form">
    <input id="text" placeholder="输入任务后回车" autocomplete="off">
    <button type="submit">添加</button>
  </form>
  <ul id="list"></ul>
  <p class="empty" id="empty">还没有任务，先添加一条。</p>
  <div class="foot"><span id="count">0 项 · 0 已完成</span>
    <button type="button" id="clear">清空</button></div>
</main>
<script>
(function () {{
  'use strict';
  var items = [];
  var list = document.getElementById('list');
  var empty = document.getElementById('empty');
  var count = document.getElementById('count');

  function paint() {{
    list.innerHTML = '';
    items.forEach(function (item) {{
      var li = document.createElement('li');
      if (item.done) li.className = 'done';
      var span = document.createElement('span');
      span.textContent = item.text;
      var toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.textContent = item.done ? '撤销' : '完成';
      toggle.addEventListener('click', function () {{
        item.done = !item.done;
        paint();
      }});
      var remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '删除';
      remove.addEventListener('click', function () {{
        items = items.filter(function (x) {{ return x !== item; }});
        paint();
      }});
      li.appendChild(span);
      li.appendChild(toggle);
      li.appendChild(remove);
      list.appendChild(li);
    }});
    empty.style.display = items.length ? 'none' : 'block';
    var done = items.filter(function (x) {{ return x.done; }}).length;
    count.textContent = items.length + ' 项 · ' + done + ' 已完成';
  }}

  document.getElementById('form').addEventListener('submit', function (event) {{
    event.preventDefault();
    var input = document.getElementById('text');
    var text = input.value.trim();
    if (!text) return;
    items.push({{ text: text, done: false }});
    input.value = '';
    paint();
  }});

  document.getElementById('clear').addEventListener('click', function () {{
    items = [];
    paint();
  }});

  paint();
}})();
</script>
</body>
</html>
"""


class Responder:
    """按提示词内容分派响应；`retry` 场景需要一个计数器区分第几次编码。"""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.kind, _, arg = scenario.partition(":")
        self.arg = arg
        self.code_calls = 0
        self.lock = threading.Lock()

    def __call__(self, prompt: str) -> str:
        if self.kind == "slow":
            time.sleep(float(self.arg or 2))
        elif self.kind.startswith("error"):
            raise FakeHTTPError(int(self.arg or 500))

        # 顺序很重要：CODE_PROMPT 里也含「构建简报」，必须先判更具体的编码/修正模板
        if "存在若干问题" in prompt:
            return _page("修正后的任务清单", "fixed")
        if "前端代码审查员" in prompt:
            if self.kind == "issues":
                return json.dumps(
                    {"ok": False, "issues": ["缺少空列表提示", "统计文案不清楚"]},
                    ensure_ascii=False,
                )
            return '{"ok": true, "issues": []}'
        if "资深前端工程师" in prompt:
            with self.lock:
                self.code_calls += 1
                nth = self.code_calls
            if self.kind == "no_html":
                return "抱歉，我无法完成这个页面。"
            if self.kind == "retry" and nth == 1:
                return "<html><body>半成品"
            return _page("任务清单")
        if "构建简报" in prompt:
            return (
                "- 目标：让用户快速记录并勾选今天要做的事\n"
                "- 版块：标题 / 输入区 / 任务列表 / 底部统计\n"
                "- 交互：新增、勾选完成、删除、清空\n"
                "- 视觉：深色，单一青色强调色\n"
                "- 移动端：输入框与按钮换行堆叠，列表不横向溢出"
            )
        return "ok"


class Handler(BaseHTTPRequestHandler):
    server_version = "FakeLLM/1.0"

    def do_POST(self) -> None:  # noqa: N802  http.server 的命名约定
        if not self.path.rstrip("/").endswith("chat/completions"):
            self._json(404, {"error": {"message": "not found"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": {"message": "bad json"}})
            return

        messages = payload.get("messages") or []
        prompt = messages[-1].get("content", "") if messages else ""
        responder: Responder = self.server.responder  # type: ignore[attr-defined]
        try:
            text = responder(prompt)
        except FakeHTTPError as exc:
            self._json(exc.code, {"error": {"message": f"fake error {exc.code}", "type": "fake"}})
            return

        self._json(
            200,
            {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": payload.get("model") or "fake",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": max(1, len(prompt) // 4),
                    "completion_tokens": max(1, len(text) // 4),
                    "total_tokens": max(2, (len(prompt) + len(text)) // 4),
                },
            },
        )

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("models"):
            self._json(200, {"object": "list", "data": [{"id": "fake", "object": "model"}]})
            return
        self._json(404, {"error": {"message": "not found"}})

    def log_message(self, fmt: str, *args) -> None:  # 静默，避免淹没 gen_once 的输出
        return

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def build_server(port: int, scenario: str = "success") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.responder = Responder(scenario)  # type: ignore[attr-defined]
    return server


def serve(port: int, scenario: str) -> None:
    server = build_server(port, scenario)
    print(
        f"[fake-llm] listening on http://127.0.0.1:{port}/v1  scenario={scenario}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="假的 OpenAI 兼容端点，用于验证 LLM 引擎")
    parser.add_argument("--port", type=int, default=int(os.getenv("FAKE_LLM_PORT") or 8099))
    parser.add_argument(
        "--scenario",
        default=os.getenv("FAKE_LLM_SCENARIO") or "success",
        help="success / retry / issues / no_html / slow:N / error:500 / error:401",
    )
    args = parser.parse_args(argv)
    serve(args.port, args.scenario)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
