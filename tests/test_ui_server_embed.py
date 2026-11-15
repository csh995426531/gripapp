from __future__ import annotations

import json
import urllib.error
import urllib.request

from grip.service import GripService
from grip.web.app import (
    _boot_board,
    _embed_script_json,
    start_ui_server,
)


def test_ui_server_serves_panel_without_browser(service: GripService) -> None:
    httpd, url, port, svc = start_ui_server(host="127.0.0.1", port=0, svc=service)
    assert port > 0
    assert svc is service
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            html = resp.read().decode("utf-8")
            assert resp.status == 200
            assert "Grip" in html
            assert "设备" in html or "Grip" in html

        with urllib.request.urlopen(url + "api/board", timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert "resources" in data or "by_resource" in data or isinstance(data, dict)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_embed_script_json_escapes_script_breakout() -> None:
    raw = _embed_script_json(
        {
            "summary": "</script><script>alert(1)</script>",
            "html": "<div data-theme='dark'>",
        }
    )
    assert "</script>" not in raw.lower()
    assert "<script" not in raw.lower()
    assert "\\u003c" in raw
    assert json.loads(raw)["summary"].startswith("</script>")


def test_boot_board_strips_huge_task_blobs() -> None:
    board = {
        "resources": [{"name": "autodl"}],
        "resource_health": {},
        "running_count": 0,
        "running": [],
        "by_resource": {
            "autodl": [
                {
                    "id": "t1",
                    "status": "ok",
                    "summary": "x" * 5000 + "</script><b>hi</b>",
                    "command": "curl http://x" + ("y" * 500),
                }
            ]
        },
        "tasks": [],
    }
    lean = _boot_board(board)
    stub = lean["by_resource"]["autodl"][0]
    assert len(stub["summary"]) <= 200
    assert len(stub["command"]) <= 120
    embedded = "window.__GRIP_BOOT__ = " + _embed_script_json({"board": lean}) + ";"
    assert embedded.lower().count("<script") == 0
    assert "</script>" not in embedded.lower()


def test_cli_start_help_lists_menubar() -> None:
    from grip.cli import main
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            main(["--help"])
        except SystemExit as exc:
            assert exc.code == 0
    text = buf.getvalue()
    assert "start" in text
    assert "menubar" in text
