from __future__ import annotations

from grip import permissions as perms


def test_permissions_status_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GRIP_HOME", str(tmp_path))
    monkeypatch.setenv("GRIP_KEYRING", "0")
    st = perms.status()
    assert "ready" in st
    assert "items" in st
    ids = {i["id"] for i in st["items"]}
    assert ids == {"keychain", "files"}
    # Keyring disabled → keychain item not ok → gate stays on.
    assert st["gate"] is True
    assert st["ready"] is False


def test_permissions_request_unknown() -> None:
    out = perms.request("nope")
    assert out["ok"] is False
    assert out["error"] == "unknown_permission"
