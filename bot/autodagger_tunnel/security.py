from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet


def load_or_create_fernet(key_file: Path) -> Fernet:
    key_file.parent.mkdir(parents=True, exist_ok=True)

    env_key = os.getenv("FERNET_KEY", "").strip()
    if env_key:
        return Fernet(env_key.encode("utf-8"))

    if key_file.exists():
        key = key_file.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        key_file.write_bytes(key + b"\n")
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass

    return Fernet(key)
