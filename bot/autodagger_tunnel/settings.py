from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Set


def _parse_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _parse_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _parse_allowed_ids(raw: str) -> Set[int]:
    ids: Set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if item.lstrip("-").isdigit():
            ids.add(int(item))
    return ids


@dataclass(frozen=True)
class Settings:
    bot_token: str
    access_mode: str
    allowed_user_ids: Set[int]
    db_path: Path
    job_db_path: Path
    key_file: Path
    default_psk: str
    test_window_seconds: int
    ssh_connect_timeout: int
    ssh_command_timeout: int
    dagger_binary_url: str
    max_parallel_servers: int
    ssh_max_retries: int
    ssh_retry_backoff_seconds: float



def load_settings() -> Settings:
    app_base = Path(os.getenv("APP_BASE_DIR", "/opt/autodaggertunnel")).resolve()
    data_dir = Path(os.getenv("DATA_DIR", str(app_base / "data"))).resolve()

    db_path = Path(os.getenv("DB_PATH", str(data_dir / "servers.db"))).resolve()
    job_db_path = Path(os.getenv("JOB_DB_PATH", str(data_dir / "jobs.db"))).resolve()
    key_file = Path(os.getenv("KEY_FILE", str(data_dir / "secret.key"))).resolve()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    access_mode = os.getenv("ACCESS_MODE", "private").strip().lower()
    if access_mode not in {"public", "private"}:
        access_mode = "private"

    allowed_raw = os.getenv("ALLOWED_USER_IDS", "")
    allowed_user_ids = _parse_allowed_ids(allowed_raw)

    default_psk = os.getenv("DEFAULT_PSK", "123").strip() or "123"

    return Settings(
        bot_token=bot_token,
        access_mode=access_mode,
        allowed_user_ids=allowed_user_ids,
        db_path=db_path,
        job_db_path=job_db_path,
        key_file=key_file,
        default_psk=default_psk,
        test_window_seconds=_parse_int("TEST_WINDOW_SECONDS", 75),
        ssh_connect_timeout=_parse_int("SSH_CONNECT_TIMEOUT", 12),
        ssh_command_timeout=_parse_int("SSH_COMMAND_TIMEOUT", 45),
        max_parallel_servers=_parse_int("MAX_PARALLEL_SERVERS", 3),
        ssh_max_retries=_parse_int("SSH_MAX_RETRIES", 3),
        ssh_retry_backoff_seconds=_parse_float("SSH_RETRY_BACKOFF_SECONDS", 1.5),
        dagger_binary_url=os.getenv(
            "DAGGER_BINARY_URL",
            "https://github.com/itsFLoKi/daggerConnect/releases/download/v1.5.1/DaggerConnect",
        ).strip(),
    )
