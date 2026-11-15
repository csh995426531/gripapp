from __future__ import annotations

from datetime import datetime, timedelta, timezone

from grip.tunnel.channels import (
    DIRECT_INTERVAL_SEC,
    RELAY_INTERVAL_SEC,
    alive,
    choose_channel,
)


def _at(seconds_ago: float) -> str:
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return moment.isoformat()


def test_backup_failure_drops_multi_channel_while_lan_stays_first() -> None:
    tun = {
        "enabled": True,
        "channels": {
            "lan": {"ok": True, "at": _at(5)},
            "direct": {"ok": False, "at": _at(5)},
            "relay": {"ok": False, "at": _at(5)},
        },
    }
    decision = choose_channel(tun)
    assert decision["path"] == "lan"
    assert decision["status"] == "reachable"
    assert decision["multi"] is False


def test_backup_recovery_adds_multi_channel_again() -> None:
    tun = {
        "enabled": True,
        "channels": {
            "lan": {"ok": True, "at": _at(5)},
            "direct": {"ok": False, "at": _at(5)},
            "relay": {"ok": True, "at": _at(5)},
        },
    }
    decision = choose_channel(tun)
    assert decision["path"] == "lan"
    assert decision["multi"] is True
    assert decision["relay"] is True


def test_stale_backup_success_does_not_keep_the_tag() -> None:
    record = {"ok": True, "at": _at(RELAY_INTERVAL_SEC * 2.5 + 5)}
    assert alive(record, RELAY_INTERVAL_SEC) is False
    tun = {
        "channels": {
            "lan": {"ok": False, "at": _at(5)},
            "relay": record,
            "direct": {"ok": True, "at": _at(DIRECT_INTERVAL_SEC * 2.5 + 5)},
        }
    }
    decision = choose_channel(tun)
    assert decision["multi"] is False
    assert decision["path"] == "down"


def test_lan_miss_falls_through_to_direct_then_relay() -> None:
    tun = {
        "channels": {
            "lan": {"ok": False, "fail": 2, "at": _at(1)},
            "direct": {"ok": True, "fail": 0, "at": _at(1)},
            "relay": {"ok": True, "fail": 0, "at": _at(1)},
        }
    }
    assert choose_channel(tun)["path"] == "tunnel"
    tun["channels"]["direct"] = {"ok": False, "fail": 2, "at": _at(1)}
    assert choose_channel(tun)["path"] == "relay"


def test_one_miss_keeps_the_channel_and_the_second_drops_it() -> None:
    from grip.tunnel.channels import fold_sample

    now = datetime.now(timezone.utc)
    held = fold_sample({"ok": True, "fail": 0, "at": _at(1)}, False, now=now)
    assert held["ok"] is True
    assert held["fail"] == 1
    dropped = fold_sample(held, False, now=now)
    assert dropped["ok"] is False
    restored = fold_sample(dropped, True, now=now)
    assert restored["ok"] is True
    assert restored["fail"] == 0
