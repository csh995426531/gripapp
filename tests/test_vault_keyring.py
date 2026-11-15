from __future__ import annotations

from pathlib import Path

import pytest

from grip import key_store
from grip.vault import Vault


def test_vault_file_backend_when_keyring_off(grip_env: Path) -> None:
    v = Vault(root=grip_env)
    assert v.backend == "file"
    assert (grip_env / "master.key").exists()
    v.put("ssh:x", "pw")
    assert Vault(root=grip_env).get("ssh:x") == "pw"


def test_vault_migrates_master_key_to_keyring(
    grip_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIP_KEYRING", "0")
    v0 = Vault(root=grip_env)
    v0.put("ssh:mig", "secret-mig")
    key_path = grip_env / "master.key"
    assert key_path.exists()
    raw_key = key_path.read_bytes().strip()

    store: dict[str, bytes] = {}

    monkeypatch.setenv("GRIP_KEYRING", "1")
    monkeypatch.setattr(key_store, "keyring_enabled", lambda: True)
    monkeypatch.setattr(
        key_store, "get_secret", lambda account: store.get(account)
    )

    def fake_set(account: str, value: bytes) -> bool:
        store[account] = value
        return True

    monkeypatch.setattr(key_store, "set_secret", fake_set)

    v1 = Vault(root=grip_env)
    assert v1.backend == "keyring"
    assert v1.get("ssh:mig") == "secret-mig"
    assert not key_path.exists()
    assert store["master"].strip() == raw_key

    v2 = Vault(root=grip_env)
    assert v2.backend == "keyring"
    assert v2.get("ssh:mig") == "secret-mig"


def test_vault_fresh_key_goes_to_keyring(
    grip_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store: dict[str, bytes] = {}
    monkeypatch.setenv("GRIP_KEYRING", "1")
    monkeypatch.setattr(key_store, "keyring_enabled", lambda: True)
    monkeypatch.setattr(
        key_store, "get_secret", lambda account: store.get(account)
    )

    def fake_set(account: str, value: bytes) -> bool:
        store[account] = value
        return True

    monkeypatch.setattr(key_store, "set_secret", fake_set)

    v = Vault(root=grip_env)
    assert v.backend == "keyring"
    assert "master" in store
    assert not (grip_env / "master.key").exists()
    v.put("ssh:a", "x")
    assert Vault(root=grip_env).get("ssh:a") == "x"
