from __future__ import annotations

import pytest

from grip.service import GripService


def test_update_ssh_endpoint_change_requires_new_secret(service: GripService) -> None:
    service.resources.add_ssh(
        name="autodl",
        host="old.example.com",
        user="root",
        password="old-secret",
        port=10001,
    )
    service.resources.apply_metrics(
        "autodl",
        {"cpu_used_pct": 9, "mem_used_pct": 8},
        sampled_at="2026-01-01T00:00:00+00:00",
    )
    with pytest.raises(ValueError, match="endpoint_changed_requires_credentials"):
        service.resources.update_ssh("autodl", host="new.example.com", port=20002)

    # Old password must still be intact after rejected update.
    assert service.vault.get("ssh:autodl") == "old-secret"
    cur = service.resources.get("autodl")
    assert cur is not None
    assert cur.meta.get("host") == "old.example.com"
    assert (cur.meta.get("metrics") or {}).get("cpu_used_pct") == 9


def test_update_ssh_endpoint_change_with_password_clears_old_metrics(
    service: GripService,
) -> None:
    service.resources.add_ssh(
        name="autodl",
        host="old.example.com",
        user="root",
        password="old-secret",
        port=10001,
    )
    service.resources.apply_metrics(
        "autodl",
        {"cpu_used_pct": 9, "mem_used_pct": 8},
        sampled_at="2026-01-01T00:00:00+00:00",
    )
    service.resources.update_ssh(
        "autodl",
        host="new.example.com",
        port=20002,
        password="new-secret",
    )
    cur = service.resources.get("autodl")
    assert cur is not None
    assert cur.meta["host"] == "new.example.com"
    assert cur.meta["port"] == 20002
    assert service.vault.get("ssh:autodl") == "new-secret"
    assert "metrics" not in cur.meta
    assert "metrics_series" not in cur.meta


def test_update_ssh_same_endpoint_can_keep_password(service: GripService) -> None:
    service.resources.add_ssh(
        name="box",
        host="h.example.com",
        user="u",
        password="secret",
        port=22,
    )
    service.resources.update_ssh("box", os_name="linux")
    assert service.vault.get("ssh:box") == "secret"
