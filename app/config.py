"""运行期配置：集中读取环境变量，其它模块只 `from app.config import settings`。

刻意不做热加载：配置改动走「改环境变量 → 重启服务」，避免运行中行为漂移。

能力档（`LLM_PROFILE`）：一个档位就是一份 `KEY=VALUE` 文件，里面放
`LLM_BASE_URL / LLM_MODEL / LLM_API_KEY` 等变量。启动时先读档位文件再把值注入
环境，**已存在的环境变量优先**，所以「临时覆盖某个值」不需要改档位文件。
档位文件只存在服务器上（目录默认 `~/.atoms/llm`，可用 `LLM_PROFILE_DIR` 改），
不进仓库；档位名会出现在 `/health`，密钥不会。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

log = logging.getLogger("atoms.config")

TRUTHY = {"1", "true", "yes", "on"}

# 档位名只允许这些字符：它会被拼进文件路径，必须挡住 ../ 之类的穿越
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
# 档位文件里只认「大写变量名=值」，与 systemd EnvironmentFile 的写法一致
PROFILE_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


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


def profile_dir() -> Path:
    """档位文件所在目录：默认 `~/.atoms/llm`，服务器可用 `LLM_PROFILE_DIR` 指到 `/etc/atoms/llm`。"""
    raw = (os.getenv("LLM_PROFILE_DIR") or "").strip()
    base = Path(raw).expanduser() if raw else Path.home() / ".atoms" / "llm"
    return base.resolve()


def _parse_profile_file(path: Path) -> dict[str, str]:
    """解析 `KEY=VALUE` 文件：忽略注释与空行，去掉成对引号，其余原样保留。

    刻意不支持 export / 变量插值 / 多行——档位文件只该放平铺的配置项，
    能被 systemd 的 EnvironmentFile 直接读，两边语义一致。
    """
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        match = PROFILE_LINE_RE.match(text)
        if not match:
            log.warning("档位文件里有无法解析的行，已跳过：%s", text[:40])
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_profile() -> tuple[str, Path | None]:
    """按 `LLM_PROFILE` 装载档位，返回 (档位名, 档位文件路径)。

    找不到或名字非法时直接抛错：宁可启动失败并留下一条明确的日志，
    也不要"以为在用 A 档、其实在跑 B 档"。
    """
    name = (os.getenv("LLM_PROFILE") or "").strip()
    if not name:
        return "", None

    if not PROFILE_NAME_RE.match(name):
        raise RuntimeError(
            f"LLM_PROFILE 取值非法：{name!r}；只允许字母、数字、下划线与短横线，最长 32 字符"
        )

    path = profile_dir() / f"{name}.env"
    if not path.is_file():
        raise RuntimeError(
            f"LLM_PROFILE={name} 但找不到档位文件 {path}；"
            f"可用 scripts/switch_model.sh list 查看已有档位，或 init {name} 生成模板"
        )

    for key, value in _parse_profile_file(path).items():
        if (os.getenv(key) or "").strip():
            continue  # 显式环境变量优先，方便临时覆盖单项而不动档位文件
        os.environ[key] = value
    return name, path


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
    # 能力档：`llm_profile` 是档位名（只用于展示与日志），文件路径便于排障
    llm_profile: str
    llm_profile_file: Path | None
    # LLM 调用缓存：按 prompt 哈希落盘，命中就不发请求
    cache_dir: Path
    llm_cache: bool
    llm_cache_max_entries: int

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
    # 必须最先执行：档位里的 LLM_* 要赶在下面读取之前注入环境
    profile, profile_file = load_profile()
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
        llm_profile=profile,
        llm_profile_file=profile_file,
        cache_dir=Path(os.getenv("LLM_CACHE_DIR") or (workspace_dir / ".cache")).resolve(),
        llm_cache=_flag("LLM_CACHE", True),
        llm_cache_max_entries=_int("LLM_CACHE_MAX_ENTRIES", 200),
    )


settings = load_settings()
