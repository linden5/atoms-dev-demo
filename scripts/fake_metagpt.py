"""假的 metagpt 包：在没有 Python 3.11 / metagpt / API Key 的机器上验证引擎。

它只替换"模型返回的文本"这一个环节，`app/engine/metagpt_engine.py` 自身全是真代码：

    懒加载探测 → _build_role() 动态建类 → PROMPT 组装 → 模型返回
    → extract_html 抽取 → 落 index.html / meta.json → 三阶段 emit

因此能验证到的是：提示词模板、HTML 抽取规则、Role 与 LLM 两条路径、失败信息、
产物落盘与元数据 —— 唯独验证不了模型本身的质量（这本来也需要真 key）。

场景（SCENARIO）：
    success        正常返回，正文带 ```html 围栏，前后还有干扰文字（测抽取）
    no_html        返回不含 HTML 的文本 → 引擎抛 ValueError（失败路径可视化）
    role_broken    Role 构造抛 TypeError → 引擎自动回退 metagpt.llm.LLM
    slow:N         N 秒后才返回 → 验证 TASK_TIMEOUT 与任务取消

用法：
    from fake_metagpt import install          # scripts/ 已在 sys.path 上
    install("success")
    from app.engine.metagpt_engine import metagpt_available
    assert metagpt_available()[0] is True

命令行入口（不启动服务，只跑一次生成）：
    python scripts/gen_once.py "做一个番茄钟" --fake-metagpt success
"""

from __future__ import annotations

import asyncio
import html
import sys
import types

FAKE_VERSION = "0.8.99-fake"
SCENARIOS = ("success", "no_html", "role_broken", "slow")

_state: dict = {"scenario": "success", "delay": 0.0, "prompts": []}


# ---------- 被引擎调用的假实现 ----------


class FakeMessage:
    """代替 metagpt.schema.Message，引擎只用到 .content。"""

    def __init__(self, content: str = "", role: str = "assistant", **kwargs: object) -> None:
        self.content = content
        self.role = role

    def __repr__(self) -> str:
        return f"FakeMessage(role={self.role!r}, chars={len(self.content)})"


class FakeAction:
    """代替 metagpt.actions.Action：引擎里的 BuildPage 继承它并覆写 run。"""

    name = "FakeAction"

    def __init__(self, **kwargs: object) -> None:
        pass

    async def _aask(self, prompt: str) -> str:
        _state["prompts"].append(prompt)
        if _state["delay"]:
            await asyncio.sleep(_state["delay"])
        if _state["scenario"] == "no_html":
            return "这个需求我需要更多信息才能实现，请补充目标用户与交互细节。"
        return _model_reply(prompt)


class FakeRole:
    """代替 metagpt.roles.Role：引擎用 set_actions 注册动作、用 run 驱动。"""

    name = "FakeRole"

    def __init__(self, **kwargs: object) -> None:
        if _state["scenario"] == "role_broken":
            # 模拟 MetaGPT 版本差异导致的构造不兼容，触发引擎的 LLM 回退分支
            raise TypeError("fake: Role 构造失败（模拟 API 不兼容）")
        self.actions: list = []

    def set_actions(self, actions: list) -> "FakeRole":
        self.actions = list(actions)
        return self

    async def run(self, message: FakeMessage) -> FakeMessage:
        requirement = getattr(message, "content", str(message))
        target = self.actions[0] if self.actions else FakeAction
        action = target() if isinstance(target, type) else target
        return FakeMessage(content=await action.run(requirement), role="assistant")


class FakeLLM:
    """代替 metagpt.llm.LLM：引擎的 fallback 路径直接 aask(prompt)。"""

    def __init__(self, **kwargs: object) -> None:
        pass

    async def aask(self, prompt: str) -> str:
        _state["prompts"].append(prompt)
        if _state["delay"]:
            await asyncio.sleep(_state["delay"])
        return _model_reply(prompt) + "\n<!-- 由 FakeLLM fallback 路径返回 -->"


# ---------- 模型返回内容 ----------


def _requirement_of(prompt: str) -> str:
    """提示词模板末段是 `需求：...`，取出来回显，顺便证明模板被真实组装过。"""
    marker = "需求："
    if marker in prompt:
        return prompt.rsplit(marker, 1)[1].strip()
    return prompt.strip()[:80]


def _model_reply(prompt: str) -> str:
    """刻意在代码块前后加解释文字，用来验证 extract_html 的"取围栏内 HTML"逻辑。"""
    requirement = html.escape(_requirement_of(prompt))
    # 把引擎真实送进来的 prompt 规模写进注释：多轮上下文有没有拼进去，看这行就知道
    meta = f"prompt_chars={len(prompt)} user_turns={prompt.count('用户：')}"
    return f"""好的，我先说明一下实现思路：单文件、内联样式、无外部依赖。

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FAKE MetaGPT 生成结果</title>
<style>
:root{{color-scheme:dark;--bg:#0b0f14;--panel:#141a22;--line:#232d3a;--text:#e6edf3;--muted:#9ba7b4;--a0:#6366f1;--a1:#22d3ee}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;color:var(--text);
  background:radial-gradient(900px 520px at 18% 0%,#171f2a,#0b0f14 65%);
  font-family:system-ui,-apple-system,"Segoe UI","PingFang SC",sans-serif}}
.card{{width:min(520px,100%);padding:24px;border:1px solid var(--line);border-radius:16px;background:var(--panel);
  box-shadow:0 30px 70px -40px rgba(0,0,0,.7)}}
.badge{{display:inline-block;font-size:11px;letter-spacing:.14em;color:var(--a1);
  border:1px solid rgba(34,211,238,.35);border-radius:999px;padding:2px 9px}}
h1{{margin:12px 0 8px;font-size:19px;line-height:1.45}}
.req{{margin:0 0 18px;padding:10px 12px;border-left:2px solid var(--a0);background:rgba(99,102,241,.08);
  border-radius:6px;color:var(--muted);font-size:12.5px;line-height:1.6;word-break:break-word}}
.box{{border:1px solid var(--line);border-radius:12px;padding:16px;text-align:center}}
#val{{font-size:40px;font-variant-numeric:tabular-nums;
  background:linear-gradient(135deg,var(--text),var(--a1));-webkit-background-clip:text;background-clip:text;color:transparent}}
button{{cursor:pointer;border-radius:10px;border:1px solid var(--line);background:transparent;color:var(--text);
  font-size:14px;padding:8px 16px;transition:transform .15s,border-color .15s}}
button:hover{{transform:translateY(-1px);border-color:var(--a0)}}
.foot{{margin:16px 0 0;font-size:11.5px;color:var(--muted);line-height:1.6}}
</style>
</head>
<body>
<!-- fake-metagpt {meta} -->
<main class="card">
  <span class="badge">FAKE METAGPT · 引擎代码为真实路径</span>
  <h1>这条产物由 mock 的「模型返回」驱动</h1>
  <p class="req">{requirement}</p>
  <div class="box">
    <p id="val">0</p>
    <button id="go" type="button">点我 +1</button>
  </div>
  <p class="foot">页面只用于验证：提示词模板、HTML 抽取、落盘与预览链路都真实执行。</p>
</main>
<script>
(function () {{
  var n = 0;
  var out = document.getElementById('val');
  document.getElementById('go').addEventListener('click', function () {{
    n += 1;
    out.textContent = String(n);
  }});
}})();
</script>
</body>
</html>
```

以上页面不引用任何外部资源，可直接打开。
"""


# ---------- 注入 ----------


def install(scenario: str = "success", delay: float = 0.0) -> str:
    """把假 metagpt 写入 sys.modules，必须在引擎探测之前调用。

    scenario 支持 `slow:5` 这种带参数的写法，等同于 delay=5。
    """
    name, _, extra = scenario.partition(":")
    if name not in SCENARIOS:
        raise ValueError(f"未知场景 {scenario!r}，可选：{', '.join(SCENARIOS)}（slow 可写 slow:5）")

    _state["scenario"] = name
    _state["delay"] = float(extra) if extra else delay
    _state["prompts"] = []

    pkg = types.ModuleType("metagpt")
    pkg.__version__ = FAKE_VERSION
    pkg.__path__ = []  # 标记为 package，便于 `from metagpt.x import y` 的常规解析

    roles = types.ModuleType("metagpt.roles")
    roles.Role = FakeRole
    actions = types.ModuleType("metagpt.actions")
    actions.Action = FakeAction
    schema = types.ModuleType("metagpt.schema")
    schema.Message = FakeMessage
    llm = types.ModuleType("metagpt.llm")
    llm.LLM = FakeLLM

    pkg.roles, pkg.actions, pkg.schema, pkg.llm = roles, actions, schema, llm
    sys.modules.update(
        {
            "metagpt": pkg,
            "metagpt.roles": roles,
            "metagpt.actions": actions,
            "metagpt.schema": schema,
            "metagpt.llm": llm,
        }
    )
    return FAKE_VERSION


def recorded_prompts() -> list[str]:
    """引擎真实组装并送进"模型"的完整提示词，用于核对模板。"""
    return list(_state["prompts"])


def reset() -> None:
    _state.update({"scenario": "success", "delay": 0.0, "prompts": []})
