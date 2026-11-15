from __future__ import annotations

import json
from pathlib import Path

from grip.cli import main as cli_main
from grip.monitor import probe_one, ssh_responds, tcp_open
from grip.resources import Resource
from grip.service import GripService


def test_tcp_open_localhost_fails_closed_port() -> None:
    assert tcp_open("127.0.0.1", 1, timeout=0.3) is False


def test_watch_once_metrics_flag(service: GripService, monkeypatch) -> None:
    monkeypatch.setattr(service, "probe_resources", lambda kind=None: {"status": "success", "checked": 0, "ok": 0, "results": []})
    calls: list[str | None] = []

    def _sample(name=None):
        calls.append(name)
        return {"status": "success", "samples": []}

    monkeypatch.setattr(service, "sample_metrics", _sample)

    with_m = service.watch_once(sync=False, metrics=True)
    assert "metrics" in with_m
    assert len(calls) == 1

    without = service.watch_once(sync=False, metrics=False)
    assert "metrics" not in without
    assert len(calls) == 1


def test_cli_watch_no_metrics(grip_env: Path, monkeypatch, capsys) -> None:
    svc = GripService()
    seen: dict[str, bool] = {}

    def _watch_once(*, sync: bool = False, metrics: bool = True):
        seen["sync"] = sync
        seen["metrics"] = metrics
        return {"probe": {"status": "success"}, "board": {}}

    monkeypatch.setattr("grip.cli.GripService", lambda: svc)
    monkeypatch.setattr(svc, "watch_once", _watch_once)

    assert cli_main(["watch", "--once", "--no-metrics"]) == 0
    assert seen == {"sync": False, "metrics": False}
    assert "metrics" not in json.loads(capsys.readouterr().out)

    assert cli_main(["watch", "--once", "--metrics"]) == 0
    assert seen == {"sync": False, "metrics": True}


def test_probe_ssh_updates_status(service: GripService, sample_key: Path, monkeypatch) -> None:
    service.resources.add_ssh(
        name="lan1", host="127.0.0.1", user="u", key_file=sample_key, port=22
    )
    monkeypatch.setattr("grip.monitor.ssh_responds", lambda host, port, timeout=2.0: True)
    out = service.probe_resources(kind="ssh")
    assert out["status"] == "success"
    assert out["checked"] >= 1
    r = service.resources.get("lan1")
    assert r is not None
    assert r.status == "reachable"
    assert r.secret_id == "ssh:lan1"  # must not wipe vault pointer
    assert "last_probe_at" in r.meta


def test_probe_all_flips_unreachable_to_reachable(
    service: GripService, sample_key: Path, monkeypatch
) -> None:
    """Menubar tick uses probe_all so reconnect flips without waiting on SSH metrics."""
    from grip.monitor import probe_all

    service.resources.add_ssh(
        name="lan2", host="127.0.0.1", user="u", key_file=sample_key, port=22
    )
    service.resources.set_status("lan2", "unreachable")
    monkeypatch.setattr("grip.monitor.ssh_responds", lambda host, port, timeout=2.0: True)
    out = probe_all(service.resources, kind="ssh")
    assert out["checked"] >= 1
    assert out["unreachable"] == 0
    r = service.resources.get("lan2")
    assert r is not None
    assert r.status == "reachable"


def test_ssh_responds_rejects_handshake_without_banner() -> None:
    import socket
    import threading

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    held: list[socket.socket] = []

    def accept() -> None:
        conn, _ = srv.accept()
        held.append(conn)

    threading.Thread(target=accept, daemon=True).start()
    try:
        assert ssh_responds("127.0.0.1", port, timeout=0.4) is False
    finally:
        for conn in held:
            conn.close()
        srv.close()


def test_probe_tunnel_handshake_without_banner_is_unreachable(monkeypatch) -> None:
    monkeypatch.setattr("grip.monitor.ssh_responds", lambda *a, **k: False)
    monkeypatch.setattr("grip.monitor.tcp_open", lambda *a, **k: True)
    resource = Resource(
        name="Windows主机",
        kind="ssh",
        status="reachable",
        meta={
            "host": "192.168.5.120",
            "lan_host": "192.168.5.120",
            "port": 22,
            "tunnel": {
                "enabled": True,
                "path": "tunnel",
                "tunnel_host": "10.77.0.3",
                "tunnel_port": 22,
                "rendezvous_url": "https://151.145.68.4:443",
            },
        },
        secret_id=None,
    )
    result = probe_one(resource, timeout=0.2)
    assert result.status == "unreachable"
    assert result.path == "down"
    assert "没有回应" in result.detail
    assert "tunnel ok" not in result.detail


def test_probe_switches_back_to_lan_when_lan_answers(monkeypatch) -> None:
    monkeypatch.setattr(
        "grip.monitor.ssh_responds",
        lambda host, port, timeout=0: host == "192.168.5.120",
    )
    resource = Resource(
        name="Windows主机",
        kind="ssh",
        status="unreachable",
        meta={
            "host": "192.168.5.120",
            "lan_host": "192.168.5.120",
            "port": 22,
            "tunnel": {
                "enabled": True,
                "path": "relay",
                "node_id": "node-1",
                "rendezvous_url": "https://151.145.68.4:443",
                "relay_proven": True,
            },
        },
        secret_id=None,
    )
    result = probe_one(resource, timeout=0.2)
    assert result.status == "reachable"
    assert result.path == "lan"
    assert result.detail == "lan ok 192.168.5.120:22"


def test_probe_uses_live_relay_heartbeat_when_lan_is_down(monkeypatch) -> None:
    from datetime import datetime, timezone

    monkeypatch.setattr("grip.monitor.ssh_responds", lambda *a, **k: False)
    fresh = datetime.now(timezone.utc).isoformat()
    resource = Resource(
        name="Windows主机",
        kind="ssh",
        status="unreachable",
        meta={
            "host": "192.168.5.120",
            "lan_host": "192.168.5.120",
            "port": 22,
            "tunnel": {
                "enabled": True,
                "path": "down",
                "channels": {"relay": {"ok": True, "at": fresh}},
            },
        },
        secret_id=None,
    )
    result = probe_one(resource, timeout=0.2)
    assert result.status == "reachable"
    assert result.path == "relay"


def test_probe_one_lan_miss_does_not_drop_a_healthy_lan(monkeypatch) -> None:
    from datetime import datetime, timezone

    monkeypatch.setattr("grip.monitor.ssh_responds", lambda *a, **k: False)
    fresh = datetime.now(timezone.utc).isoformat()
    resource = Resource(
        name="Mac-mini",
        kind="ssh",
        status="reachable",
        meta={
            "host": "192.168.5.15",
            "lan_host": "192.168.5.15",
            "port": 22,
            "tunnel": {
                "enabled": True,
                "path": "lan",
                "channels": {
                    "lan": {"ok": True, "fail": 0, "at": fresh},
                    "relay": {"ok": True, "fail": 0, "at": fresh},
                },
            },
        },
        secret_id=None,
    )
    result = probe_one(resource, timeout=0.2)
    assert result.status == "reachable"
    assert result.path == "lan"


def test_probe_skips_stopped_cloud() -> None:
    r = Resource(
        name="oci:x",
        kind="oracle_instance",
        status="stopped",
        meta={"public_ip": "1.2.3.4"},
        secret_id=None,
    )
    pr = probe_one(r)
    assert pr.status == "stopped"
    assert "skip L2" in pr.detail
