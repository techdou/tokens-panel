"""配置读取：从环境变量 / .env 文件加载，并处理首次启动的密钥生成。"""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ENV_FILE = BASE_DIR / ".env"


def _load_dotenv() -> None:
    """简易 .env 加载器，避免引入 python-dotenv 依赖。"""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _ensure_master_key() -> str:
    """返回主加密密钥；若 .env 里为空则生成并写回。"""
    key = os.environ.get("MASTER_KEY", "").strip()
    if key:
        return key

    key = Fernet.generate_key().decode()
    _write_env_value("MASTER_KEY", key)
    os.environ["MASTER_KEY"] = key
    return key


def _write_env_value(key: str, value: str) -> None:
    """把某个键值写回 .env（不存在则创建；已存在则替换整行）。"""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    out, found = [], False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


# ---- 对外配置项 ----
DATA_DIR.mkdir(parents=True, exist_ok=True)

MASTER_KEY: str = _ensure_master_key()
ADMIN_PASSWORD: str = os.environ.get("ADMIN_PASSWORD", "change-me-to-a-strong-password")
PORT: int = int(os.environ.get("PORT", "8000"))
DB_PATH: Path = DATA_DIR / os.environ.get("DB_FILE", "tokens.db")
REFRESH_INTERVAL_MINUTES: int = int(os.environ.get("REFRESH_INTERVAL_MINUTES", "15"))
DAILY_REPORT_TIME: str = os.environ.get("DAILY_REPORT_TIME", "09:00")
SESSION_TTL_HOURS: int = int(os.environ.get("SESSION_TTL_HOURS", "720"))
