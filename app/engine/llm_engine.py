"""真实生成引擎（LLM）：串三次调用完成「规划 → 编码 → 自检」，零新增依赖。

为什么不用 MetaGPT：`metagpt==0.8.2` 需要 Python 3.11 且依赖 faiss/老版 aiohttp，
安装与单次生成时延都不可控（5–10 分钟）。这里直接用 OpenAI 兼容的
`/chat/completions`，用自己的三次调用拼出同样的"简报 → 交付 → 验证"语义，
时延 30–90 秒、失败可重试、配置只有三个环境变量。

契约与 MOCK/MetaGPT 完全一致（见 `app/engine/base.py`）：只往 workdir 写文件、
不改数据库、阶段上报尽力而为；产物结构交给 `scan_artifacts()` 解释。

取消语义：HTTP 调用在线程里跑，取消协程时线程会跑完当前请求再退出
（无法中途 kill），但任务状态会立刻进入 cancelled，用户侧无感。这是刻意取舍：
为了零依赖不引入 httpx，换来的代价只是取消后的那一次请求仍会耗 token。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app import llm_cache
from app.config import settings
from app.engine.base import EmitFn
from app.engine.html_utils import (
    external_refs,
    extract_html,
    inspect_html,
    placeholder_hits,
)
from app.engine.prompts import CODE_PROMPT, FIX_PROMPT, PLAN_PROMPT, REVIEW_PROMPT, render

log = logging.getLogger("atoms.engine.llm")

MAX_ATTEMPTS = 2          # 编码阶段最多尝试两次（第二次带上失败原因）
RETRY_BACKOFF_S = 1.5     # 限流/5xx 的退避
ISSUE_LIMIT = 5
# 自检温度：不能用 0，智谱等厂商的 OpenAI 兼容模式会拒绝 temperature=0（400）
REVIEW_TEMPERATURE = 0.01


def _endpoint() -> str:
    """实际请求的完整 URL；报错时原样打出来，省得靠猜 base_url 拼对没拼对。"""
    return f"{settings.llm_base_url}/chat/completions"


def llm_available() -> tuple[bool, str]:
    """探测 LLM 是否已配置。只读配置，不发请求。"""
    if not settings.llm_base_url or not settings.llm_model:
        return False, "未设置 LLM_BASE_URL / LLM_MODEL"
    if not settings.llm_api_key:
        return False, "未设置 LLM_API_KEY"
    return True, f"{settings.llm_model} @ {settings.llm_base_url}"


class LLMEngine:
    """单个模型、三次调用、一个产物文件。"""

    name = "llm"

    def __init__(self) -> None:
        available, detail = llm_available()
        if not available:
            raise RuntimeError(
                f"LLM 引擎不可用（{detail}）；可设 ENGINE_MODE=mock 退回演示引擎，"
                "或补齐 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY"
            )
        self.detail = detail
        self.model = settings.llm_model
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.steps: list[dict[str, Any]] = []
        self.cache_hits = 0

    # ---------- 对外入口 ----------

    async def run(self, task_id: str, prompt: str, workdir: Path, emit: EmitFn) -> None:
        workdir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()

        # 1) 规划：出一份用户能看懂的中文简报
        await emit("planning")
        brief = await self._step(
            "planning",
            PLAN_PROMPT,
            {"requirement": prompt},
            max_tokens=900,
        )

        # 2) 编码：出单文件应用，不合格就带着原因重试一次
        await emit("coding")
        html, problems = await self._build_html(prompt, brief)

        # 3) 自检：结构化 review，有问题再修一次（不无限重试，避免演示时越修越慢）
        await emit("checking")
        issues = await self._review(html, prompt)
        fixed = False
        if issues:
            candidate = await self._fix(html, issues)
            fixed = candidate != html
            html = candidate

        title = _title_from_plan(brief) or _fallback_title(prompt)
        (workdir / "index.html").write_text(html, encoding="utf-8")
        (workdir / "meta.json").write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "prompt": prompt,
                    "engine": "llm",
                    "model": self.model,
                    "title": title,
                    "brief": brief.strip(),
                    "chars": len(html),
                    "issues": issues,
                    "fixed": fixed,
                    "retry_problems": problems,
                    "external_refs": external_refs(html),
                    "placeholders": placeholder_hits(html),
                    "tokens": self.usage,
                    "steps": self.steps,
                    "profile": settings.llm_profile,
                    "cache_hits": self.cache_hits,
                    "elapsed_s": round(time.perf_counter() - started, 1),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        log.info(
            "LLM 生成完成 task=%s model=%s chars=%d issues=%d cache_hits=%d elapsed=%.1fs 产物目录=%s",
            task_id,
            self.model,
            len(html),
            len(issues),
            self.cache_hits,
            time.perf_counter() - started,
            workdir.resolve(),
        )

    # ---------- 三个阶段 ----------

    async def _build_html(self, prompt: str, brief: str) -> tuple[str, list[str]]:
        """编码 + 体检 + 重试。两次都不合格才抛错，错误信息写成人话。"""
        problems: list[str] = []
        text = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            extra = ""
            if problems:
                extra = "\n\n上一次输出不合格：" + "；".join(problems) + "。请重新输出完整 HTML。"
            text = await self._step(
                "coding",
                CODE_PROMPT,
                {"brief": brief.strip(), "requirement": prompt},
                max_tokens=settings.llm_max_tokens,
                suffix=extra,
            )
            html = extract_html(text)
            ok, why = inspect_html(html)
            if ok:
                return html, problems  # type: ignore[return-value]
            problems.append(why)
            log.warning("第 %d 次编码不合格：%s", attempt, why)

        raise RuntimeError(
            "模型没返回可用的完整页面（" + "；".join(problems) + "）。"
            "可以再试一次，或把需求写得更具体（例如说明要哪些控件）。"
        )

    async def _review(self, html: str, prompt: str) -> list[str]:
        """返回需要修正的问题列表；解析失败按"没有问题"处理，不阻断交付。

        温度用 0.01 而不是 0：部分厂商（如智谱的 OpenAI 兼容模式）明确不接受
        `temperature=0`，会返回 400 让自检被静默跳过——那等于审查没跑，问题却看不出来。
        """
        try:
            text = await self._step(
                "checking",
                REVIEW_PROMPT,
                {"html": html},
                max_tokens=600,
                temperature=REVIEW_TEMPERATURE,
            )
        except RuntimeError as exc:  # 审查本身失败不该让整个任务失败
            log.warning("自检调用失败，跳过：%s", exc)
            return []

        data = _parse_json_object(text)
        if data is None:
            log.warning("自检输出不是 JSON，跳过修正：%s", (text or "")[:120])
            return []
        if data.get("ok") is True:
            return []

        issues = [str(item).strip() for item in (data.get("issues") or []) if str(item).strip()]
        # 本地体检补两条硬问题：外链会被 CSP 拦，占位符是明显的半成品
        if external_refs(html):
            issues.append("引用了外链资源，预览时会被拦掉，请改成内联")
        if placeholder_hits(html):
            issues.append("页面里还有未替换的占位内容")
        return issues[:ISSUE_LIMIT]

    async def _fix(self, html: str, issues: list[str]) -> str:
        """带问题清单重写一次；修完仍不合格就保留原稿（宁可原稿能跑）。"""
        bullet = "\n".join(f"- {item}" for item in issues)
        try:
            text = await self._step(
                "checking",
                FIX_PROMPT,
                {"issues": bullet, "html": html},
                max_tokens=settings.llm_max_tokens,
            )
        except RuntimeError as exc:
            log.warning("修正调用失败，保留原稿：%s", exc)
            return html

        candidate = extract_html(text)
        ok, why = inspect_html(candidate)
        if not ok:
            log.warning("修正稿不合格（%s），保留原稿", why)
            return html
        log.info("自检修正生效，问题数=%d", len(issues))
        return candidate  # type: ignore[return-value]

    # ---------- HTTP ----------

    async def _step(
        self,
        stage: str,
        template: str,
        values: dict[str, str],
        *,
        max_tokens: int,
        temperature: float | None = None,
        suffix: str = "",
    ) -> str:
        """渲染模板 → 查缓存 → 未命中才调 chat/completions → 记录耗时与 token。"""
        content = render(template, **values) + suffix
        identity = self._cache_identity(temperature, max_tokens)
        key = llm_cache.make_key(namespace="llm", identity=identity, prompt=content)

        cached = llm_cache.lookup(key)
        began = time.perf_counter()
        if cached is not None:
            self.cache_hits += 1
            text, usage = cached, {}
            log.info("缓存命中 stage=%s chars=%d", stage, len(cached))
        else:
            text, usage = await self._chat(content, max_tokens=max_tokens, temperature=temperature)
            llm_cache.store(
                key=key,
                namespace="llm",
                identity=identity,
                prompt=content,
                raw=text,
                meta={"stage": stage, "model": self.model},
            )

        self.steps.append(
            {
                "stage": stage,
                "ms": round((time.perf_counter() - began) * 1000),
                "chars": len(text or ""),
                "tokens": usage.get("total_tokens", 0),
                "cached": cached is not None,
            }
        )
        for name in self.usage:
            self.usage[name] += int(usage.get(name) or 0)
        return text

    def _cache_identity(self, temperature: float | None, max_tokens: int) -> str:
        """缓存身份：换档位、换模型、换端点、换采样参数都必须重新生成。

        少任何一项都会出现"切了档位却拿到上一档输出"的假象，那比不缓存更糟。
        """
        return "|".join(
            (
                f"engine={self.name}",
                f"profile={settings.llm_profile or '-'}",
                f"model={settings.llm_model}",
                f"base={settings.llm_base_url}",
                f"temp={settings.llm_temperature if temperature is None else temperature}",
                f"max_tokens={max_tokens}",
                f"effort={settings.llm_reasoning_effort or '-'}",
                f"extra={json.dumps(settings.llm_extra_body, sort_keys=True) if settings.llm_extra_body else '-'}",
                # 测试可用 ATOMS_CACHE_TAG 隔离不同场景，正常部署留空
                f"tag={os.getenv('ATOMS_CACHE_TAG') or '-'}",
            )
        )

    async def _chat(
        self,
        user_content: str,
        *,
        max_tokens: int,
        temperature: float | None = None,
    ) -> tuple[str, dict[str, Any]]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你只按要求的格式输出，不闲聊、不解释。"},
                {"role": "user", "content": user_content},
            ],
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if settings.llm_reasoning_effort:
            # 部分推理模型（如智谱 GLM-5.3 系列）无法关闭思考，默认强度会把 max_tokens
            # 全部吃在 reasoning_tokens 上、只返回空 content；low 能显著压缩思考开销。
            payload["reasoning_effort"] = settings.llm_reasoning_effort
        if settings.llm_extra_body:
            # 厂商专属参数透传（DeepSeek 关思考用 thinking.type，别的厂商各有各的写法）
            payload.update(settings.llm_extra_body)
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._post, payload),
                    timeout=settings.llm_timeout + 10,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001  统一映射成人话后决定是否重试
                last_error = _humanize(exc)
                if not _retryable(exc) or attempt == MAX_ATTEMPTS:
                    break
                log.warning("LLM 调用失败（第 %d 次）：%s，%.1fs 后重试", attempt, last_error, RETRY_BACKOFF_S)
                await asyncio.sleep(RETRY_BACKOFF_S)
        raise RuntimeError(last_error)

    def _post(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """同步 HTTP（在线程里跑）。刻意只用标准库，避免为一次 POST 引入 httpx。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            _endpoint(),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=settings.llm_timeout) as response:  # noqa: S310
            raw = response.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"LLM 返回的不是 JSON：{raw[:200]}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM 返回缺少 choices：{raw[:200]}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None:
            content = choices[0].get("text") or ""
        usage = data.get("usage") or {}
        text = str(content or "")
        if not text.strip():
            # 空 content 几乎都是"思考把预算吃光"，直接说清楚，别让它伪装成"输出过短"
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
            if reasoning:
                raise RuntimeError(
                    f"模型把输出预算都用在思考上（reasoning_tokens={reasoning}，"
                    f"max_tokens={payload.get('max_tokens')}），返回内容为空；"
                    "调大 LLM_MAX_TOKENS，或设置 LLM_REASONING_EFFORT=low 降低思考强度"
                )
        return text, usage


# ---------- 小工具 ----------


def _retryable(exc: Exception) -> bool:
    """限流、5xx、网络抖动可重试；401/400 这类重试也没用。"""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    if isinstance(exc, (urllib.error.URLError, TimeoutError, asyncio.TimeoutError)):
        return True
    return "LLM 返回的不是 JSON" in str(exc)


def _humanize(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return f"LLM 响应超时（{settings.llm_timeout}s），可调大 LLM_TIMEOUT 或缩短需求"
    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001 读不到 body 也不影响提示
            detail = ""
        if exc.code in (401, 403):
            return f"LLM 鉴权失败（HTTP {exc.code}），请检查 LLM_API_KEY"
        if exc.code == 404:
            # 端点拼错是 404 的头号原因，直接把实际请求的 URL 摆出来，别让人猜
            return (
                f"LLM 接口不存在（HTTP 404）：{_endpoint()}；"
                "确认 LLM_BASE_URL 是否包含正确的版本路径"
                "（OpenAI/DeepSeek 需 /v1，智谱是 /api/paas/v4，且引擎会自动补 /chat/completions）"
            )
        if exc.code == 429:
            return "LLM 限流或额度不足（HTTP 429），稍后重试或换模型"
        return f"LLM 请求失败（HTTP {exc.code}）{('：' + detail) if detail else ''}"
    if isinstance(exc, urllib.error.URLError):
        return f"连不上 LLM 端点：{exc.reason}（{_endpoint()}）"
    return f"{type(exc).__name__}: {exc}"


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出第一个 JSON 对象，容忍 ```json 围栏与前后废话。"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _title_from_plan(brief: str) -> str:
    """取简报里「目标：」那一行做版本名，前端版本卡片直接显示它。"""
    for line in (brief or "").splitlines():
        text = line.strip().lstrip("-").strip()
        if text.startswith(("目标：", "目标:")):
            text = text.split("：", 1)[-1] if "：" in text else text.split(":", 1)[-1]
            return text.strip()[:40]
    first = (brief or "").strip().splitlines()
    return first[0].lstrip("- ").strip()[:40] if first else ""


def _fallback_title(prompt: str) -> str:
    """简报没给出目标时，用需求前 24 字兜底。"""
    text = " ".join((prompt or "").split())
    return text[:24] or "生成应用"
