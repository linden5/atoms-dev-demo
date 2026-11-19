"""LLM 调用缓存：同一个 prompt 重复生成时直接复用上一次的模型返回。

为什么需要：调试期同一个需求会被反复跑（改前端、改状态机、换入口点一次），
每次都重新调模型既慢又费额度，而且模型输出会抖，导致排查时"变量太多"。
把「输入 → 模型返回的原始文本」落盘后，命中路径是确定的字节级复用。

只缓存**模型返回的原始文本**，不缓存产物文件：产物结构仍由引擎和
`scan_artifacts()` 解释，缓存不会绕过抽取、体检、自检与修正逻辑。
换句话说，缓存命中只省掉"等模型"这一段，其余代码路径一模一样。

键 = sha256(schema | namespace | identity | prompt)：
- `namespace` 区分调用场景（`llm` 的单次步骤、`metagpt` 的整轮生成）；
- `identity` 由调用方给出（引擎 + 档位 + 模型 + 端点 + 采样参数），换档位或换端点
  必然不命中，避免"切了能力档却拿到旧档位的输出"。

安全：缓存目录在 `workspace/.cache/`（随 `WORKSPACE_DIR` 走），已在 `.gitignore` 里；
落盘内容只有模型输出与 prompts 摘要，**不写 api_key**。缓存读写失败一律降级为
"未命中"，缓存不能成为生成失败的原因。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger("atoms.cache")

# 缓存格式版本：改动文件结构时递增，旧文件自然全部失效而不是解析出错
CACHE_SCHEMA = 1

_stats = {"hit": 0, "miss": 0, "write": 0, "error": 0}


def make_key(*, namespace: str, identity: str, prompt: str) -> str:
    """把「场景 + 身份 + 输入」压成一个稳定的 64 位十六进制键。"""
    digest = hashlib.sha256()
    for part in (f"v{CACHE_SCHEMA}", namespace, identity, prompt):
        digest.update(part.encode("utf-8", errors="replace"))
        digest.update(b"\x00")  # 分隔符，防止 ("a","bc") 与 ("ab","c") 撞键
    return digest.hexdigest()


def _path(key: str) -> Path:
    return settings.cache_dir / f"{key}.json"


def lookup(key: str) -> str | None:
    """命中返回模型原始文本；未命中、禁用、损坏都返回 None。"""
    if not settings.llm_cache:
        return None

    path = _path(key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _stats["miss"] += 1
        return None
    except Exception as exc:  # noqa: BLE001 缓存坏了只能算未命中，不能让任务失败
        _stats["error"] += 1
        log.warning("缓存读取失败，按未命中处理：%s（%s）", path.name, exc)
        return None

    raw = payload.get("raw")
    if payload.get("schema") != CACHE_SCHEMA or not isinstance(raw, str) or not raw:
        _stats["miss"] += 1
        return None

    _stats["hit"] += 1
    try:
        os.utime(path, None)  # 刷新访问时间，让淘汰策略按"最近使用"保留
    except OSError:
        pass
    return raw


def store(
    *,
    key: str,
    namespace: str,
    identity: str,
    prompt: str,
    raw: str,
    meta: dict[str, Any] | None = None,
) -> None:
    """写缓存；任何 IO 问题都只记日志，不影响本次生成。"""
    if not settings.llm_cache or not raw:
        return

    path = _path(key)
    payload = {
        "schema": CACHE_SCHEMA,
        "key": key,
        "namespace": namespace,
        "identity": identity,
        "prompt_chars": len(prompt),
        "raw_chars": len(raw),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "meta": meta or {},
        "raw": raw,
    }

    tmp_name = ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写：先写同目录临时文件再 replace，读方永远看不到半截 JSON
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(path.parent), delete=False, suffix=".tmp"
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False)
            tmp_name = handle.name
        os.replace(tmp_name, path)
        _stats["write"] += 1
    except Exception as exc:  # noqa: BLE001
        _stats["error"] += 1
        log.warning("缓存写入失败，跳过：%s（%s）", path.name, exc)
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        return

    _prune()


def _prune() -> None:
    """超过上限就按最近使用时间淘汰最旧的，防止调试把磁盘写满。"""
    limit = settings.llm_cache_max_entries
    try:
        files = sorted(
            settings.cache_dir.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
        )
    except OSError:
        return
    for stale in files[: max(0, len(files) - limit)]:
        try:
            stale.unlink()
        except OSError:
            continue


def clear() -> int:
    """清空缓存，返回删除的文件数。"""
    removed = 0
    try:
        files = list(settings.cache_dir.glob("*.json"))
    except OSError:
        return 0
    for item in files:
        try:
            item.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def summary(*, active: bool | None = None) -> dict[str, Any]:
    """/health 用：只报状态与计数，不暴露目录与键。

    `active=False` 表示当前引擎根本不会调模型（MOCK），此时如实回 `enabled=false`，
    免得把"没命中过"误读成"缓存失效"。
    """
    enabled = settings.llm_cache if active is None else (settings.llm_cache and active)
    entries = 0
    if enabled:
        try:
            entries = len(list(settings.cache_dir.glob("*.json")))
        except OSError:
            entries = 0
    return {
        "enabled": enabled,
        "entries": entries,
        "hit": _stats["hit"],
        "miss": _stats["miss"],
        "max_entries": settings.llm_cache_max_entries,
    }
