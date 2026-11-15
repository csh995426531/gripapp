from __future__ import annotations

import time
from pathlib import Path

from grip.service import GripService
from grip.tunnel.route import route_notice


def test_background_multi_host_board(service: GripService, sample_key: Path, monkeypatch) -> None:
    for name in ("host-a", "host-b", "host-c"):
        service.resources.add_ssh(
            name=name, host="127.0.0.1", user="ubuntu", key_file=sample_key
        )

    def fake_run(resource, vault, command, timeout_sec=60):
        from grip.ssh_exec import ExecResult

        time.sleep(0.05)
        return ExecResult(0, f"ok-{resource.name}", "")

    monkeypatch.setattr("grip.service.run_ssh", fake_run)

    ids = []
    for name in ("host-a", "host-b", "host-c"):
        out = service.run_command(name, "uname -a", background=True)
        assert out["status"] == "running"
        assert out["path"] == "lan"
        assert out["route"] == "局域网"
        assert out["expect_sec"] == 15
        ids.append(out["task_id"])

    board = service.board()
    assert set(board["by_resource"].keys()) >= {"host-a", "host-b", "host-c"}
    assert board["running_count"] >= 1

    # wait workers
    deadline = time.time() + 3
    while time.time() < deadline:
        tasks = [service.get_task(i)["task"] for i in ids]
        statuses = [t["status"] for t in tasks]
        assert all(t.get("route") == "局域网" for t in tasks)
        if all(s == "success" for s in statuses):
            break
        time.sleep(0.05)
    else:
        raise AssertionError(statuses)

    board2 = service.board()
    for name in ("host-a", "host-b", "host-c"):
        assert any(t["status"] == "success" for t in board2["by_resource"][name])


def test_route_notice_follows_ui_language(grip_env) -> None:
    from grip.i18n import set_lang

    set_lang("en")
    try:
        relay = route_notice("relay")
        assert relay["route"] == "Rendezvous relay"
        assert "Wait at least 90 seconds" in relay["note"]
    finally:
        set_lang("zh")
    assert route_notice("lan")["route"] == "局域网"


def test_route_notice_names_the_slow_relay() -> None:
    relay = route_notice("relay")
    assert relay["route"] == "汇合点中继"
    assert relay["expect_sec"] == 90
    assert "不要在几秒内当成失败" in relay["note"]
    direct = route_notice("tunnel")
    assert direct["route"] == "汇合点直连"
    assert route_notice("down")["expect_sec"] == 0
