from __future__ import annotations

from pathlib import Path

from grip.native_pick import default_ssh_key_dir, resolve_start_dir


def test_default_ssh_key_dir_prefers_dot_ssh(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    ssh = home / ".ssh"
    ssh.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert default_ssh_key_dir() == str(ssh)


def test_default_ssh_key_dir_falls_back_to_home(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert default_ssh_key_dir() == str(home)


def test_resolve_start_dir_expanduser(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    ssh = home / ".ssh"
    ssh.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert resolve_start_dir("~/.ssh") == str(ssh.resolve())
    assert resolve_start_dir(None) is None
