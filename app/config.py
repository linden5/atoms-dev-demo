"""运行期配置：集中读取环境变量，其它模块只 `from app.config import settings`。

刻意不做热加载：配置改动走「改环境变量 → 重启服务」，避免运行中行为漂移。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

TRUTHY = {"1", "true", "yes", "on"}


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in TRUTHY


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


ENGINE_MODE_AUTO = "auto"
ENGINE_MODE_MOCK = "mock"
ENGINE_MODE_METAGPT = "metagpt"
ENGINE_MODE_LLM = "llm"
ENGINE_MODES = (ENGINE_MODE_AUTO, ENGINE_MODE_MOCK, ENGINE_MODE_METAGPT, ENGINE_MODE_LLM)


@dataclass(frozen=True)
class Settings:
    """一次性从环境变量解析出的只读配置。"""

    base_dir: Path
    data_dir: Path
    workspace_dir: Path
    log_dir: Path
    db_path: Path
    engine_mode: str
    task_timeout: int
    max_concurrency: int
    max_artifact_bytes: int
    max_single_file_bytes: int
    # LLM 引擎（OpenAI 兼容端点）：全空即视为未配置，auto 会跳过它
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: int
    llm_max_tokens: int
    llm_temperature: float

    @property
    def mock_engine(self) -> bool:
        """显式指定 MOCK 时为 True；auto 交给引擎选择逻辑判断。"""
        return self.engine_mode == ENGINE_MODE_MOCK

    @property
    def llm_configured(self) -> bool:
        """三个必填项都在才算可用：地址、模型、Key。

        刻意不把 Key 之外的项设默认值——宁可在 auto 下安静地降级成 MOCK，
        也不要在演示时因为一个打错的默认域名把任务全部挂掉。
        """
        return bool(self.llm_base_url and self.llm_model and self.llm_api_key)


def _engine_mode() -> str:
    """引擎选择，两代开关并存。

    1. `ENGINE_MODE=llm|mock|metagpt|auto`（S8 起，推荐）：显式四态。
    2. `MOCK_ENGINE`（S4 遗留）：未设置=auto，1=强制 mock，0=强制 metagpt。

    `auto` 的意义：交付物既能在配了 LLM 的服务器上真实生成，也能在没有 Key
    的机器上直接跑起来（降级 MOCK），不需要改部署配置。
    """
    raw = os.getenv("ENGINE_MODE")
    if raw and raw.strip().lower() in ENGINE_MODES:
        return raw.strip().lower()

    legacy = os.getenv("MOCK_ENGINE")
    if legacy is None or legacy.strip() == "":
        return ENGINE_MODE_AUTO
    return ENGINE_MODE_MOCK if legacy.strip().lower() in TRUTHY else ENGINE_MODE_METAGPT


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR") or (BASE_DIR / "data")).resolve()
    workspace_dir = Path(os.getenv("WORKSPACE_DIR") or (BASE_DIR / "workspace")).resolve()
    return Settings(
        base_dir=BASE_DIR,
        data_dir=data_dir,
        workspace_dir=workspace_dir,
        log_dir=BASE_DIR / "logs",
        db_path=data_dir / "app.db",
        engine_mode=_engine_mode(),
        task_timeout=_int("TASK_TIMEOUT", 600),
        max_concurrency=_int("MAX_CONCURRENCY", 1),
        max_artifact_bytes=_int("MAX_ARTIFACT_BYTES", 50 * 1024 * 1024),
        max_single_file_bytes=_int("MAX_SINGLE_FILE_BYTES", 5 * 1024 * 1024),
        llm_base_url=(os.getenv("LLM_BASE_URL") or "").strip().rstrip("/"),
        llm_api_key=(os.getenv("LLM_API_KEY") or "").strip(),
        llm_model=(os.getenv("LLM_MODEL") or "").strip(),
        llm_timeout=_int("LLM_TIMEOUT", 120),
        llm_max_tokens=_int("LLM_MAX_TOKENS", 8000),
        llm_temperature=_float("LLM_TEMPERATURE", 0.6),
    )


settings = load_settings()
