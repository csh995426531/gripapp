from __future__ import annotations

import json
from pathlib import Path

from grip import autostart
from grip.cli import main as cli_main
from grip.service import GripService


def test_update_ssh_host_and_password(
    service: GripService, sample_key: Path, tmp_path: Path
) -> None:
    service.resources.add_ssh(
        name="ed1",
        host="10.0.0.1",
        user="u",
        key_file=sample_key,
        port=22,
    )
    r = service.resources.update_ssh(
        "ed1", host="10.0.0.2", user="admin", port=2222, password="secret-pass"
    )
    assert r.meta["host"] == "10.0.0.2"
    assert r.meta["user"] == "admin"
    assert r.meta["port"] == 2222
    assert r.meta["auth"] == "password"
    assert service.vault.get("ssh:ed1") == "secret-pass"


def test_autostart_status_roundtrip(tmp_path: Path, monkeypatch) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    app = tmp_path / "Grip.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "Grip").write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(autostart, "_agents_dir", lambda: agents)
    monkeypatch.setattr(autostart, "default_app_path", lambda: app)
    monkeypatch.setattr(
        autostart.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )

    assert autostart.is_enabled() is False
    st = autostart.enable()
    assert st["enabled"] is True
    assert Path(st["plist"]).is_file()
    st2 = autostart.disable()
    assert st2["enabled"] is False


def test_mcp_update_ssh_never_echoes_password(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    import grip.mcp_server as mcp_mod

    monkeypatch.setattr(mcp_mod, "get_service", lambda: service)
    service.resources.add_ssh(
        name="ed2",
        host="10.0.0.1",
        user="u",
        key_file=sample_key,
        port=22,
    )
    out = mcp_mod.update_ssh(
        name="ed2", host="10.0.0.9", password="super-secret-pw"
    )
    blob = json.dumps(out)
    assert "super-secret-pw" not in blob
    assert out["labels"]["host"] == "10.0.0.9"
    assert out["labels"]["auth"] == "password"
    assert service.vault.get("ssh:ed2") == "super-secret-pw"


def test_mcp_autostart_tools(tmp_path: Path, monkeypatch) -> None:
    import grip.mcp_server as mcp_mod

    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    app = tmp_path / "Grip.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "Grip").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(autostart, "_agents_dir", lambda: agents)
    monkeypatch.setattr(autostart, "default_app_path", lambda: app)
    monkeypatch.setattr(
        autostart.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )

    st = mcp_mod.autostart_status()
    assert st["enabled"] is False
    assert st["label"] == "local.grip.menubar"
    on = mcp_mod.set_autostart(True)
    assert on["enabled"] is True
    off = mcp_mod.set_autostart(False)
    assert off["enabled"] is False


def test_cli_resource_update_ssh(
    sample_key: Path, grip_env: Path, capsys
) -> None:
    # CLI constructs GripService from GRIP_HOME (set by grip_env fixture).
    seeded = GripService()
    seeded.resources.add_ssh(
        name="cli1",
        host="10.0.0.1",
        user="u",
        key_file=sample_key,
        port=22,
    )
    rc = cli_main(
        [
            "resource-update-ssh",
            "--name",
            "cli1",
            "--host",
            "10.0.0.5",
            "--user",
            "root",
            "--port",
            "2200",
            "--password",
            "cli-secret",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "cli-secret" not in json.dumps(out)
    assert out["labels"]["host"] == "10.0.0.5"
    assert out["labels"]["user"] == "root"
    assert out["labels"]["port"] == 2200
    assert out["labels"]["auth"] == "password"
    assert GripService().vault.get("ssh:cli1") == "cli-secret"


def test_cli_autostart_status(tmp_path: Path, monkeypatch, capsys) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    app = tmp_path / "Grip.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "Grip").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(autostart, "_agents_dir", lambda: agents)
    monkeypatch.setattr(autostart, "default_app_path", lambda: app)
    monkeypatch.setattr(
        autostart.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )

    assert cli_main(["autostart"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["enabled"] is False
    assert st["label"] == "local.grip.menubar"

    assert cli_main(["autostart", "--enable"]) == 0
    on = json.loads(capsys.readouterr().out)
    assert on["enabled"] is True

    assert cli_main(["autostart", "--disable"]) == 0
    off = json.loads(capsys.readouterr().out)
    assert off["enabled"] is False
