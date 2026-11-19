"""LLM 引擎的提示词模板。

分三段而不是一次到位，是为了让前端的「三阶段」有真实语义：
planning 出书面简报（用户能看懂要做什么），coding 出 HTML，
checking 做一次结构化自检并在必要时修正。

模板用 `__XXX__` 占位符 + `render()` 而不是 `str.format`，
因为提示词里本身有 JSON 花括号，format 会炸。
"""

from __future__ import annotations

PLAN_PROMPT = """你是产品经理。请为下面这个网页应用需求写一份**简短**构建简报。

只输出 5 行以内的中文条目，每行以「- 」开头，顺序为：
- 目标：一句话说明这个应用替用户解决什么
- 版块：页面从上到下有哪些区域（用 / 分隔）
- 交互：至少 1 个真正改变数据状态的交互（不是纯展示）
- 视觉：配色与字体风格（不引入外部字体）
- 移动端：375px 宽度下如何不崩

不要输出 HTML，不要解释，不要客套。

需求：__REQUIREMENT__
"""

CODE_PROMPT = """你是资深前端工程师，现在要把需求实现成一个**单文件**网页应用。

构建简报（可参考，不必逐条照抄）：
__BRIEF__

需求：__REQUIREMENT__

硬性要求（违反任意一条都会导致预览失败）：
1. 只输出一个完整 HTML 文档，从 <!doctype html> 开始到 </html> 结束，CSS 与 JS 全部内联；
2. 不要输出任何解释文字、不要用 Markdown 代码围栏；
3. 不引用任何外部资源（CDN、外链字体、外链图片、外链脚本），需要图标用内联 SVG；
4. 界面中文，`<meta name="viewport" content="width=device-width, initial-scale=1">` 必须有，375px 宽度下仍可用；
5. 必须有真实可用的交互（增删改、筛选、计算、切换状态等），数据用 JS 变量维护，不依赖网络与后端；
6. 预览运行在禁用存储的沙箱里：禁止使用 localStorage、sessionStorage、document.cookie（访问会直接抛错）；
   如需"刷新后数据保留"，平台已在页面注入 `window.__ATOMS__`（含 dataUrl 与 token），用它持久化：
   保存 `fetch(window.__ATOMS__.dataUrl + "?token=" + window.__ATOMS__.token, {method:"PUT",
   headers:{"Content-Type":"application/json"}, body: JSON.stringify(状态对象)})`，
   启动时 `fetch(同地址 + "?token=" + window.__ATOMS__.token)` 后 `response.json()` 读回；
7. 不允许出现 `[Company name]`、`Lorem ipsum` 这类占位内容。
"""

REVIEW_PROMPT = """你是前端代码审查员。检查下面这个单文件网页应用是否可以交付。

逐项检查：
1. 结构完整：有 <!doctype>、<html>、</html>，没有截断；
2. 有真实交互：至少一个交互会改变界面状态（不是只有 hover 动效）；
3. 没有外链资源：script/link/img 的 src 或 href 不含 http(s)://；
4. 没有占位内容：没有 `[Company Name]`、`Lorem ipsum` 之类；
5. 移动端：有 viewport meta，窄屏不会横向溢出；
6. 中文界面，文案通顺。

只输出 JSON，不要代码块、不要解释，格式固定为：
{"ok": true 或 false, "issues": ["问题1", "问题2"]}
没有问题时 ok 为 true 且 issues 为空数组。

页面：
__HTML__
"""

FIX_PROMPT = """你是资深前端工程师。下面这个单文件网页应用存在若干问题，请修正。

需要修正的问题：
__ISSUES__

约束不变：单文件、CSS/JS 内联、不引用任何外部资源、中文界面、有真实交互、375px 可用。
只输出修正后的完整 HTML（从 <!doctype html> 到 </html>），不要解释。

当前页面：
__HTML__
"""


def render(template: str, **values: str) -> str:
    """把 `__KEY__` 替换成对应内容；未提供的占位符原样保留，方便排障。"""
    text = template
    for key, value in values.items():
        text = text.replace(f"__{key.upper()}__", value)
    return text
