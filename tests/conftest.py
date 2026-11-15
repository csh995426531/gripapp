from __future__ import annotations

import os
from pathlib import Path

import pytest

from grip.service import GripService
from grip.vault import Vault


@pytest.fixture()
def grip_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "grip-home"
    home.mkdir()
    monkeypatch.setenv("GRIP_HOME", str(home))
    # Isolate tests from macOS Keychain / real user secrets.
    monkeypatch.setenv("GRIP_KEYRING", "0")
    return home


@pytest.fixture()
def service(grip_env: Path) -> GripService:
    vault = Vault(root=grip_env)
    db = grip_env / "grip.db"
    return GripService(db_path=db, vault=vault)


@pytest.fixture(autouse=True)
def _tcp_open_ok_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests mock SSH; assume LAN TCP open unless a test overrides."""
    monkeypatch.setattr("grip.monitor.tcp_open", lambda *a, **k: True)
    monkeypatch.setattr("grip.service.tcp_open", lambda *a, **k: True)
    monkeypatch.setattr("grip.metrics.tcp_open", lambda *a, **k: True)
    monkeypatch.setattr("grip.tunnel.ops.tcp_open", lambda *a, **k: True)


@pytest.fixture(autouse=True)
def _reset_lan_cooldown() -> None:
    from grip.tunnel.path import reset_lan_cooldown_for_tests

    reset_lan_cooldown_for_tests()
    yield
    reset_lan_cooldown_for_tests()


@pytest.fixture(autouse=True)
def _remote_agent_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most unit tests mock plain run_ssh; enable agent explicitly in agent tests."""
    monkeypatch.setenv("GRIP_REMOTE_AGENT", "0")


@pytest.fixture(autouse=True)
def _wg_priv_off_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never pop macOS admin dialogs during pytest."""
    monkeypatch.setenv("GRIP_WG_PRIV", "0")
    from grip.tunnel.wgembed import reset_privilege_cooldown_for_tests

    reset_privilege_cooldown_for_tests()
    yield
    reset_privilege_cooldown_for_tests()


@pytest.fixture()
def sample_key(tmp_path: Path) -> Path:
    # Not a real key — only for vault/storage tests (SSH exec tests mock/skip)
    p = tmp_path / "id_test"
    p.write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nTESTKEYMATERIALNOTREAL\n-----END OPENSSH PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    return p
