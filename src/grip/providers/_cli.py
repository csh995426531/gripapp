from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any


def which(bin_name: str) -> bool:
    return shutil.which(bin_name) is not None


def run_json(cmd: list[str], *, timeout: int = 120) -> Any:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = (msg[0] if msg else f"exit {proc.returncode}")[:200]
        raise RuntimeError(f"{cmd[0]} failed: {tail}")
    out = (proc.stdout or "").strip()
    if not out:
        return None
    # tolerate leading noise before JSON
    if "{" in out:
        out = out[out.index("{") :]
    elif "[" in out:
        out = out[out.index("[") :]
    return json.loads(out)


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", name or "host")
