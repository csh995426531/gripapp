from __future__ import annotations

import json
from pathlib import Path

from grip.panel_proxy import (
    clear_panel_meta,
    panel_meta_path,
    read_panel_url,
    try_panel_run_command,
    write_panel_meta,
)


def test_panel_meta_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRIP_HOME", str(tmp_path))
    write_panel_meta(url="http://127.0.0.1:8765", pid=123)
    assert read_panel_url() == "http://127.0.0.1:8765/"
    data = json.loads(panel_meta_path().read_text(encoding="utf-8"))
    assert data["pid"] == 123
    clear_panel_meta()
    assert read_panel_url() is None


def test_try_panel_run_returns_none_when_no_panel(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GRIP_HOME", str(tmp_path))
    assert try_panel_run_command("box", "uptime") is None
