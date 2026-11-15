from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

from grip import __version__
from grip import autostart
from grip.cli import main as cli_main
from grip.install_menubar import (
    info_plist,
    install_applications_symlink,
    install_menubar,
    install_start_menu_launcher,
    launcher_script,
    package_menubar,
    package_readme,
    refresh_app_bundle,
    refresh_win_launcher,
    win_launcher_script,
)


def test_refresh_app_bundle_writes_launcher_and_plist(tmp_path: Path) -> None:
    app = tmp_path / "apps" / "Grip.app"
    out = refresh_app_bundle(app, version="9.9.9")
    assert out == app.resolve()
    exe = app / "Contents" / "MacOS" / "Grip"
    assert exe.is_file()
    assert os.access(exe, os.X_OK)
    text = exe.read_text(encoding="utf-8")
    assert "pwd -P" in text
    assert "GRIP_MENUBAR_DIRECT=1" in text
    assert "run_native_menubar" in text
    plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
    assert "LSUIElement" in plist
    assert "9.9.9" in plist
    assert "local.grip.menubar" in plist


def test_info_plist_defaults_to_package_version() -> None:
    assert __version__ in info_plist()
    assert "pwd -P" in launcher_script()


def test_install_applications_symlink(tmp_path: Path) -> None:
    app = tmp_path / "repo" / "apps" / "Grip.app"
    refresh_app_bundle(app)
    dest = tmp_path / "Applications"
    result = install_applications_symlink(app, dest_dir=dest)
    assert result["ok"] is True
    link = dest / "Grip.app"
    assert link.is_symlink()
    assert link.resolve() == app.resolve()
    # Re-run replaces existing symlink
    result2 = install_applications_symlink(app, dest_dir=dest)
    assert result2["ok"] is True
    assert link.resolve() == app.resolve()


def test_install_menubar_with_autostart(tmp_path: Path, monkeypatch) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    app = tmp_path / "apps" / "Grip.app"
    monkeypatch.setattr(autostart, "_agents_dir", lambda: agents)
    monkeypatch.setattr(
        autostart.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )
    dest = tmp_path / "Applications"
    out = install_menubar(
        applications=True,
        autostart_enable=True,
        app_path=app,
        applications_dir=dest,
    )
    assert out["refreshed"] is True
    assert out["applications"]["ok"] is True
    assert out["autostart"]["enabled"] is True
    assert Path(out["autostart"]["app"]) == dest / "Grip.app"
    assert "open" in out["how_to_run"]


def test_cli_install_menubar_refresh_only(tmp_path: Path, monkeypatch, capsys) -> None:
    app = tmp_path / "apps" / "Grip.app"
    monkeypatch.setenv("GRIP_APP", str(app))
    # install_menubar.default_app_path reads GRIP_APP; refresh uses that via install_menubar()
    # but install_menubar() calls refresh_app_bundle(app_path) with None → default_app_path
    from grip import install_menubar as im

    monkeypatch.setattr(im, "default_app_path", lambda: app)
    monkeypatch.setattr(im, "repo_root", lambda: tmp_path)

    assert cli_main(["install-menubar"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["refreshed"] is True
    assert (app / "Contents" / "MacOS" / "Grip").is_file()
    assert payload["applications"] is None
    assert payload["autostart"] is None


def test_cli_install_menubar_applications(tmp_path: Path, monkeypatch, capsys) -> None:
    app = tmp_path / "apps" / "Grip.app"
    dest = tmp_path / "MyApps"
    from grip import install_menubar as im

    monkeypatch.setattr(im, "default_app_path", lambda: app)

    assert (
        cli_main(
            [
                "install-menubar",
                "--applications",
                "--applications-dir",
                str(dest),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["applications"]["ok"] is True
    assert (dest / "Grip.app").is_symlink()


def test_package_menubar_writes_unsigned_zip(tmp_path: Path) -> None:
    app = tmp_path / "apps" / "Grip.app"
    dist = tmp_path / "dist"
    out = package_menubar(dist_dir=dist, app_path=app, version="0.2.0")
    assert out["ok"] is True
    assert out["unsigned"] is True
    assert out["notarized"] is False
    zip_path = Path(out["zip"])
    assert zip_path == dist / "grip-menubar-unsigned-0.2.0.zip"
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert "README.txt" in names
        assert "apps/Grip.app/Contents/MacOS/Grip" in names
        assert "apps/Grip.app/Contents/Info.plist" in names
        readme = zf.read("README.txt").decode("utf-8")
    assert "NOT signed" in readme
    assert "Gatekeeper" in readme
    assert ".venv" in readme
    assert "notarized" in readme.lower()
    assert "Gatekeeper" in package_readme()


def test_cli_package_menubar(tmp_path: Path, monkeypatch, capsys) -> None:
    app = tmp_path / "apps" / "Grip.app"
    dist = tmp_path / "out"
    from grip import install_menubar as im

    monkeypatch.setattr(im, "default_app_path", lambda: app)
    monkeypatch.setattr(im, "default_dist_dir", lambda: dist)

    assert cli_main(["package-menubar", "--dist-dir", str(dist)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["unsigned"] is True
    assert payload["notarized"] is False
    assert Path(payload["zip"]).is_file()
    assert f"grip-menubar-unsigned-{__version__}.zip" in payload["zip"]


def test_refresh_win_launcher_writes_cmd(tmp_path: Path) -> None:
    launcher = tmp_path / "apps" / "Grip.cmd"
    out = refresh_win_launcher(launcher)
    assert out == launcher.resolve()
    text = launcher.read_text(encoding="utf-8")
    assert "GRIP_MENUBAR_DIRECT=1" in text
    assert "run_native_menubar" in text
    assert ".venv\\Scripts\\python.exe" in text
    assert "GRIP_MENUBAR_DIRECT=1" in win_launcher_script()


def test_install_start_menu_launcher(tmp_path: Path) -> None:
    src = tmp_path / "apps" / "Grip.cmd"
    refresh_win_launcher(src)
    dest = tmp_path / "Programs"
    result = install_start_menu_launcher(src, dest_dir=dest)
    assert result["ok"] is True
    copied = dest / "Grip.cmd"
    assert copied.is_file()
    assert "run_native_menubar" in copied.read_text(encoding="utf-8")


def test_autostart_windows_startup_cmd(tmp_path: Path, monkeypatch) -> None:
    startup = tmp_path / "Startup"
    launcher = tmp_path / "Grip.cmd"
    refresh_win_launcher(launcher)
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setattr(autostart, "_startup_dir", lambda: startup)
    monkeypatch.setattr(autostart, "default_app_path", lambda: launcher)
    out = autostart.enable(app_path=launcher)
    assert out["enabled"] is True
    assert out["platform"] == "win32"
    assert Path(out["startup_cmd"]).is_file()
    body = Path(out["startup_cmd"]).read_text(encoding="utf-8")
    assert str(launcher.resolve()) in body or str(launcher) in body
    out2 = autostart.disable()
    assert out2["enabled"] is False
    assert not Path(out["startup_cmd"]).is_file()
