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


@dataclass(frozen=True)
class Settings:
    """一次性从环境变量解析出的只读配置。"""

    base_dir: Path
    data_dir: Path
    workspace_dir: Path
    log_dir: Path
    db_path: Path
    mock_engine: bool
    task_timeout: int
    max_concurrency: int
    max_artifact_bytes: int
    max_single_file_bytes: int

    @property
    def engine_name(self) -> str:
        return "mock" if self.mock_engine else "metagpt"


def load_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR") or (BASE_DIR / "data")).resolve()
    workspace_dir = Path(os.getenv("WORKSPACE_DIR") or (BASE_DIR / "workspace")).resolve()
    return Settings(
        base_dir=BASE_DIR,
        data_dir=data_dir,
        workspace_dir=workspace_dir,
        log_dir=BASE_DIR / "logs",
        db_path=data_dir / "app.db",
        # S3 还没有真实引擎，默认走 MOCK；S4 提供 metagpt 后把默认值改为 False
        mock_engine=_flag("MOCK_ENGINE", True),
        task_timeout=_int("TASK_TIMEOUT", 600),
        max_concurrency=_int("MAX_CONCURRENCY", 1),
        max_artifact_bytes=_int("MAX_ARTIFACT_BYTES", 50 * 1024 * 1024),
        max_single_file_bytes=_int("MAX_SINGLE_FILE_BYTES", 5 * 1024 * 1024),
    )


settings = load_settings()
