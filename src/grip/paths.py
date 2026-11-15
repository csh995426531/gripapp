from __future__ import annotations

import os
from pathlib import Path


def grip_home() -> Path:
    raw = os.environ.get("GRIP_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".grip").resolve()


def ensure_home() -> Path:
    home = grip_home()
    home.mkdir(parents=True, mode=0o700, exist_ok=True)
    return home


def db_path() -> Path:
    return ensure_home() / "grip.db"


def status_path() -> Path:
    return ensure_home() / "status.json"


def master_key_path() -> Path:
    return ensure_home() / "master.key"
