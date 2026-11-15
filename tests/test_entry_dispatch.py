from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_entry():
    path = Path(__file__).resolve().parents[1] / "packaging" / "entry_menubar.py"
    spec = importlib.util.spec_from_file_location("grip_entry_menubar", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dispatch_mcp_arg() -> None:
    mod = _load_entry()
    assert mod._dispatch_mode(["mcp"]) == "mcp"
    assert mod._dispatch_mode(["mcp", "--verbose"]) == "mcp"


def test_dispatch_menubar_default() -> None:
    mod = _load_entry()
    assert mod._dispatch_mode([]) == "menubar"
    assert mod._dispatch_mode(["-psn_0_12345"]) == "menubar"
