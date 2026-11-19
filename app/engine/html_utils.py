"""模型输出的 HTML 抽取与体检。

抽出来共用而不是各引擎各写一份的原因：预览白屏 90% 是同一个原因
（模型把 HTML 包在 ``` 里、或者只输出了半截），两个引擎用同一套规则
就不会出现"换了引擎预览就挂"的诡异问题。

约定：这里只做纯函数，不读写文件、不碰配置，方便被脚本单独调用。
"""

from __future__ import annotations

import re

# 低于这个长度基本可以断定不是完整应用（空 <html> 骨架约 100 字符）
MIN_HTML_CHARS = 800

_FENCE = re.compile(r"```(?:html|HTML)?\s*(.*?)```", re.S)
_DOCTYPE = re.compile(r"<!doctype\s+html", re.I)
_HTML_TAG = re.compile(r"<html[\s>]", re.I)
_CLOSING = re.compile(r"</html\s*>", re.I)
_INTERACTIVE = re.compile(r"<(script|style)[\s>]", re.I)
# 预览 CSP 只放行 'self' / inline / data / blob，外链资源会被浏览器拦掉
_EXTERNAL_REF = re.compile(r"""<(?:script|link|img|iframe)\b[^>]*\b(?:src|href)\s*=\s*["']https?://""", re.I)
# 占位符：[Company name] / [Lorem ipsum] 之类。负向后行断言排除合法代码里的
# 下标取值（tokens[pos]、arr[i]），否则自检会"修正"根本不存在的问题
_PLACEHOLDER = re.compile(r"(?<![A-Za-z0-9_$\]])\[(?:[A-Za-z][A-Za-z /&'-]{2,20})\]|(?<![A-Za-z0-9_$\]])lorem ipsum", re.I)
# 预览 iframe 是 opaque origin（无 allow-same-origin），访问存储 API 会直接抛 SecurityError
_STORAGE = re.compile(r"\b(?:localStorage|sessionStorage|document\.cookie)\b")


def extract_html(text: str | None) -> str | None:
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


def inspect_html(html: str | None) -> tuple[bool, str]:
    """体检：返回 `(是否可用于预览, 人话原因)`。

    原因会被拼进重试提示词，也会进 `meta.json`，所以要能直接给用户看。
    """
    if not html:
        return False, "没有从模型输出里解析出 HTML"
    if len(html) < MIN_HTML_CHARS:
        return False, f"页面只有 {len(html)} 字符，明显不是完整应用"
    if not _INTERACTIVE.search(html):
        return False, "页面里没有 <script> 或 <style>，不像可交互应用"
    if not _CLOSING.search(html):
        return False, "HTML 没有闭合（可能被截断）"
    return True, "ok"


def external_refs(html: str) -> list[str]:
    """列出会被预览 CSP 拦掉的外链资源，用于体检提示与自检修正。"""
    return [m.group(0)[:120] for m in _EXTERNAL_REF.finditer(html or "")]


def placeholder_hits(html: str) -> list[str]:
    """找出 `[Company name]` 这类没替换的占位符。"""
    return sorted({m.group(0) for m in _PLACEHOLDER.finditer(html or "")})[:8]


def storage_hits(html: str) -> list[str]:
    """预览沙箱禁用存储；出现即列出位置，供体检与自检修正。

    注意：平台注入的 `window.__ATOMS__` 存储桥不含这些标识符，不会被误伤。
    """
    return [m.group(0) for m in _STORAGE.finditer(html or "")][:5]
