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
ENGINE_MODES = (ENGINE_MODE_AUTO, ENGINE_MODE_MOCK, ENGINE_MODE_METAGPT)


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

    @property
    def mock_engine(self) -> bool:
        """显式指定 MOCK 时为 True；auto 交给引擎选择逻辑判断。"""
        return self.engine_mode == ENGINE_MODE_MOCK


def _engine_mode() -> str:
    """`MOCK_ENGINE` 三态：未设置=auto，1=强制 mock，0=强制 metagpt。

    auto 的意义：S4 之后交付物既能在装了 MetaGPT 的服务器上真实生成，
    也能在没有依赖/没有额度的机器上直接跑起来，不需要改部署配置。
    """
    raw = os.getenv("MOCK_ENGINE")
    if raw is None or raw.strip() == "":
        return ENGINE_MODE_AUTO
    return ENGINE_MODE_MOCK if raw.strip().lower() in TRUTHY else ENGINE_MODE_METAGPT


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
    )


settings = load_settings()
