from __future__ import annotations

from pathlib import Path

import pytest

from grip.redact import assert_no_secrets, redact_text
from grip.vault import SecretNotFound, Vault


def test_vault_roundtrip(grip_env: Path) -> None:
    v = Vault(root=grip_env)
    v.put("ssh:a", "secret-value")
    assert v.get("ssh:a") == "secret-value"
    v2 = Vault(root=grip_env)
    assert v2.get("ssh:a") == "secret-value"


def test_vault_missing(grip_env: Path) -> None:
    v = Vault(root=grip_env)
    with pytest.raises(SecretNotFound):
        v.get("nope")


def test_redact_pem() -> None:
    text = "hello\n-----BEGIN RSA PRIVATE KEY-----\nABC\n-----END RSA PRIVATE KEY-----\nbye"
    out = redact_text(text)
    assert "PRIVATE KEY" not in out or "REDACTED" in out
    assert "***REDACTED_PEM***" in out


def test_assert_no_secrets_rejects_api_key_field() -> None:
    with pytest.raises(AssertionError):
        assert_no_secrets({"api_key": "x"})
