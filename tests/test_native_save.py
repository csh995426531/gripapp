from __future__ import annotations

import json
from pathlib import Path

import pytest

from grip.native_save import (
    choose_save_path,
    clear_save_path_chooser,
    display_path,
    register_save_path_chooser,
    save_export_with_panel,
    write_text_file,
)
from grip.service import GripService
from grip.web.app import start_ui_server


def teardown_function(_fn) -> None:
    clear_save_path_chooser()


def test_display_path_uses_tilde() -> None:
    target = Path.home() / "Desktop" / "grip-audit-all.jsonl"
    assert display_path(target) == "~/Desktop/grip-audit-all.jsonl"


def test_write_text_file_creates_parent(tmp_path: Path) -> None:
    dest = tmp_path / "out" / "a" / "grip-audit.jsonl"
    got = write_text_file(dest, '{"ok":true}\n')
    assert got.is_file()
    assert got.read_text(encoding="utf-8") == '{"ok":true}\n'


def test_save_export_with_panel_ok(tmp_path: Path) -> None:
    dest = tmp_path / "grip-audit-Mac-mini.jsonl"

    def chooser(default_name: str, prompt: str):
        assert default_name.endswith(".jsonl")
        assert prompt
        return str(dest), "ok"

    register_save_path_chooser(chooser)
    out = save_export_with_panel(
        body='{"resource":"Mac-mini"}\n',
        filename="grip-audit-Mac-mini.jsonl",
    )
    assert out.get("ok") is True
    assert Path(out["path"]) == dest.resolve()
    assert dest.read_text(encoding="utf-8").startswith("{")
    assert "display_path" in out


def test_save_export_cancelled() -> None:
    register_save_path_chooser(lambda _n, _p: (None, "cancelled"))
    out = save_export_with_panel(body="x\n", filename="grip-audit-all.jsonl")
    assert out == {"cancelled": True}


def test_save_export_unavailable_returns_body_for_download() -> None:
    register_save_path_chooser(lambda _n, _p: (None, "unavailable"))
    out = save_export_with_panel(
        body="line\n",
        filename="grip-audit-all.jsonl",
        media_type="application/x-ndjson; charset=utf-8",
    )
    assert out.get("native") is False
    assert out.get("body") == "line\n"
    assert out.get("filename") == "grip-audit-all.jsonl"


def test_save_export_write_error(tmp_path: Path) -> None:
    # Directory path as file target → write fails
    bad = tmp_path / "isdir"
    bad.mkdir()

    register_save_path_chooser(lambda _n, _p: (str(bad), "ok"))
    out = save_export_with_panel(body="x\n", filename="x.jsonl")
    assert "error" in out
    assert "写入失败" in out["error"]


def test_choose_save_path_uses_registered_bridge() -> None:
    register_save_path_chooser(lambda n, p: (f"/tmp/{n}", "ok"))
    path, status = choose_save_path("grip-audit-all.jsonl", prompt="选择保存位置")
    assert status == "ok"
    assert path == "/tmp/grip-audit-all.jsonl"


def test_post_audit_export_saves_via_bridge(
    service: GripService, sample_key: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import urllib.request

    service.resources.add_ssh(
        name="Mac-mini", host="127.0.0.1", user="u", key_file=sample_key
    )

    def fake_run(resource, vault, command, timeout_sec=60):
        from grip.ssh_exec import ExecResult

        return ExecResult(0, "ok\n", "")

    monkeypatch.setattr("grip.service.run_ssh", fake_run)
    service.run_command("Mac-mini", "echo hi", background=False, actor="panel")

    dest = tmp_path / "saved.jsonl"
    register_save_path_chooser(lambda _n, _p: (str(dest), "ok"))

    httpd, url, _port, _svc = start_ui_server(host="127.0.0.1", port=0, svc=service)
    try:
        req = urllib.request.Request(
            url + "api/audit/export",
            data=json.dumps({"name": "Mac-mini", "format": "jsonl"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload.get("ok") is True
        assert dest.is_file()
        assert "Mac-mini" in dest.read_text(encoding="utf-8") or payload.get("count", 0) >= 0
        assert payload.get("display_path")
    finally:
        httpd.shutdown()
        httpd.server_close()
        clear_save_path_chooser()


def test_post_audit_export_cancel_is_quiet(service: GripService) -> None:
    import urllib.request

    register_save_path_chooser(lambda _n, _p: (None, "cancelled"))
    httpd, url, _port, _svc = start_ui_server(host="127.0.0.1", port=0, svc=service)
    try:
        req = urllib.request.Request(
            url + "api/audit/export",
            data=json.dumps({"format": "jsonl"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload == {"cancelled": True}
    finally:
        httpd.shutdown()
        httpd.server_close()
        clear_save_path_chooser()
