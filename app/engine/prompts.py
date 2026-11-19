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
7. 核心逻辑必须正确：输出前用 2-3 个代表性输入（例如计算器算 "2+3"、添加一条记录）在脑中完整走查
   调用链（事件入口 → 校验 → 计算 → 渲染），确认不会被自己的校验规则误拦、结果符合预期；
8. 不允许出现 `[Company name]`、`Lorem ipsum` 这类占位内容。
"""

REVIEW_PROMPT = """你是前端代码审查员。检查下面这个单文件网页应用是否可以交付。

逐项检查：
1. 结构完整：有 <!doctype>、<html>、</html>，没有截断；
2. 有真实交互：至少一个交互会改变界面状态（不是只有 hover 动效）；
3. 没有外链资源：script/link/img 的 src 或 href 不含 http(s)://；
4. 没有占位内容：没有 `[Company Name]`、`Lorem ipsum` 之类；
5. 移动端：有 viewport meta，窄屏不会横向溢出；
6. 中文界面，文案通顺；
7. 沙箱约束：没有使用 localStorage / sessionStorage / cookie（平台注入的 window.__ATOMS__
   存储桥除外），界面没有承诺"刷新后保留"（除非用的是 __ATOMS__ 存储桥）；
8. 功能正确性：找出 1-2 个核心交互函数，用代表性输入逐行模拟执行，验证逻辑正确、
   输出符合预期、不会被校验规则误拦（例如计算类应用必须验证一个真实算式的完整路径）。

只输出 JSON，不要代码块、不要解释，格式固定为：
{"ok": true 或 false, "issues": ["问题1", "问题2"], "verified": ["已走查并验证的交互1", ...]}
没有问题时 ok 为 true、issues 为空数组，verified 列出你实际验证过的交互。

页面：
__HTML__
"""

FIX_PROMPT = """你是资深前端工程师。下面这个单文件网页应用存在若干问题，请修正。

需要修正的问题：
__ISSUES__

约束不变：单文件、CSS/JS 内联、不引用任何外部资源、中文界面、有真实交互、375px 可用；
禁止使用 localStorage / sessionStorage / cookie（预览沙箱禁用，数据只存内存或用平台注入的
window.__ATOMS__ 存储桥）。
只输出修正后的完整 HTML（从 <!doctype html> 到 </html>），不要解释。

当前页面：
__HTML__
"""

REVISION_PROMPT = """你是资深前端工程师。下面是一个已生成的单文件网页应用，用户在使用中
发现了问题或有修改需求。请在保留原有功能与风格的前提下修改代码。

用户的反馈或新需求：
__REQUIREMENT__

修改要求：
1. 先定位根因：涉及"某操作报错/无效"时，找到对应的事件处理与校验逻辑逐行走查，确认修复路径，
   不要只改表面文案；
2. 用一个代表性输入验证修复后的完整调用链确实正确；
3. 除用户要求外不要重构、不要改视觉风格、不要更换实现方式；
4. 只输出修改后的完整 HTML（从 <!doctype html> 到 </html>），不要解释、不要 Markdown 围栏；
5. 约束不变：CSS/JS 全内联、不引用任何外部资源、中文界面、375px 可用；
   禁止使用 localStorage / sessionStorage / cookie（预览沙箱禁用，数据只存内存或用
   平台注入的 window.__ATOMS__ 存储桥）。

当前页面：
__HTML__
"""


VERSION_REF_PROMPT = """用户在多轮对话中提到想基于某个历史版本继续修改。根据版本列表和用户的话，
判断用户指的是哪个版本。按内容理解（比如"能算三角函数的那版"对得上某个版本的需求摘要），
不要按字面猜数字。

版本列表：
__VERSIONS__

用户的话：__REQUIREMENT__

只输出 JSON，不要解释：{"version": 编号}，编号必须是版本列表里存在的（1 到 __TOTAL__）；
用户指的是最新版本或看不出指代时输出 {"version": null}。
"""


def render(template: str, **values: str) -> str:
    """把 `__KEY__` 替换成对应内容；未提供的占位符原样保留，方便排障。"""
    text = template
    for key, value in values.items():
        text = text.replace(f"__{key.upper()}__", value)
    return text
