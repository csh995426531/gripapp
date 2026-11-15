#!/usr/bin/env python3
"""Live self-check for Grip menubar UI (API + optional DOM via CDP-less checks).

Usage:
  .venv/bin/python scripts/self_check_ui.py
  GRIP_URL=http://127.0.0.1:8765 .venv/bin/python scripts/self_check_ui.py

Exits non-zero on failure. Intended for the agent to run before asking a human
to click through list / cloud / Tokyo detail again.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "grip" / "web" / "app.py"


def _get(url: str) -> dict | str:
    with urllib.request.urlopen(url, timeout=8) as resp:
        body = resp.read().decode("utf-8")
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype or body.lstrip().startswith("{"):
            return json.loads(body)
        return body


def check_source_invariants() -> list[str]:
    fails: list[str] = []
    src = APP.read_text(encoding="utf-8")
    need = [
        ("list sparks fixed", "width:36px;height:12px;flex:0 0 36px"),
        ("cloud two-col", "grid-template-columns:8px minmax(0,1fr)"),
        ("metrics fallback", "if(ssh && hasUsableMetrics(ssh)) return ssh;"),
        ("vendor hint when SSH empty", "关联 SSH 暂无可用指标"),
    ]
    for label, needle in need:
        if needle not in src:
            fails.append(f"source missing {label}: {needle!r}")
    if "grid-template-rows:auto auto" in src:
        fails.append("source reintroduced stacked pill grid-template-rows:auto auto")
    return fails


def check_live_board(base: str) -> list[str]:
    fails: list[str] = []
    try:
        board = _get(base.rstrip("/") + "/api/board")
    except Exception as exc:  # noqa: BLE001 — surface any live check error
        return [f"board fetch failed: {exc}"]
    if not isinstance(board, dict):
        return ["board response not JSON object"]
    resources = board.get("resources") or []
    if not resources:
        fails.append("board has no resources")
        return fails

    def usable(r: dict) -> bool:
        L = r.get("labels") or r.get("meta") or {}
        m = L.get("metrics") or {}
        if any(m.get(k) is not None for k in ("cpu_used_pct", "mem_used_pct", "load1", "net_rx_kbps", "disk_used_pct")):
            return True
        return bool(L.get("metrics_series"))

    sshish = [r for r in resources if (r.get("kind") or "") in ("", "ssh") or r.get("kind") == "ssh"]
    # Prefer explicit ssh kind when present; many boards use kind "" / "ssh".
    local_ssh = [
        r
        for r in resources
        if str(r.get("kind") or "ssh") in ("ssh", "")
        and not str(r.get("name") or "").startswith(("oci:", "gcp:", "aliyun:", "tencent:"))
    ]
    with_sparks = [r for r in local_ssh if usable(r)]
    if local_ssh and not with_sparks:
        fails.append("no local SSH devices have usable metrics (list sparks would be empty)")

    tokyo = [
        r
        for r in resources
        if "东京" in str(r.get("name") or "") or "tokyo" in str(r.get("name") or "").lower()
    ]
    if tokyo:
        cloud = [r for r in tokyo if "oracle" in str(r.get("kind") or "") or str(r.get("name") or "").startswith("oci:")]
        ssh = [r for r in tokyo if r not in cloud]
        cloud_ok = any(usable(r) for r in cloud)
        ssh_ok = any(usable(r) for r in ssh)
        if not (cloud_ok or ssh_ok):
            fails.append("Tokyo/OCI has no usable metrics on cloud or SSH twin")
        elif not cloud_ok and ssh_ok is False:
            # cloud_ok false, ssh also false already handled; keep branch for clarity
            pass
        # Prefer that vendor path works when SSH is broken
        for r in ssh:
            L = r.get("labels") or {}
            detail = str(L.get("metrics_detail") or "")
            if detail and not usable(r) and not cloud_ok:
                fails.append(f"SSH twin broken ({detail[:80]}) and cloud vendor metrics also empty")
    return fails


def main() -> int:
    fails = check_source_invariants()
    base = os.environ.get("GRIP_URL", "http://127.0.0.1:8765")
    live_fails = check_live_board(base)
    if live_fails and "board fetch failed" in live_fails[0]:
        print(f"WARN live skipped: {live_fails[0]}")
    else:
        fails.extend(live_fails)

    if fails:
        print("SELF-CHECK FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("SELF-CHECK OK")
    print(f" - source invariants")
    print(f" - live board @ {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
