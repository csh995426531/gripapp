from __future__ import annotations

from grip.tunnel.path import (
    clear_lan_failed,
    is_lan_silenced,
    mark_lan_failed,
    reset_lan_cooldown_for_tests,
    resolve_ssh_endpoint,
)


def setup_function() -> None:
    reset_lan_cooldown_for_tests()


def test_prefers_lan_when_tcp_open() -> None:
    meta = {
        "lan_host": "192.168.5.15",
        "port": 22,
        "tunnel": {
            "enabled": True,
            "node_id": "node-1",
            "rendezvous_url": "http://127.0.0.1:7700",
        },
    }
    d = resolve_ssh_endpoint(meta, tcp_open=lambda *a, **k: True)
    assert d.path == "lan"


def test_relay_when_lan_down_with_rendezvous() -> None:
    meta = {
        "lan_host": "192.168.5.15",
        "port": 22,
        "tunnel": {
            "enabled": True,
            "node_id": "node-1",
            "rendezvous_url": "http://vps.example:7700",
        },
    }
    d = resolve_ssh_endpoint(meta, tcp_open=lambda *a, **k: False)
    assert d.path == "relay"
    assert is_lan_silenced("192.168.5.15", 22)


def test_lan_silence_skips_reprobe_then_expires() -> None:
    meta = {
        "lan_host": "192.168.5.15",
        "port": 22,
        "tunnel": {
            "enabled": True,
            "node_id": "node-1",
            "rendezvous_url": "http://vps.example:7700",
            "tunnel_host": "10.8.0.2",
            "tunnel_port": 22,
        },
    }
    probes: list[tuple[str, int]] = []

    def probe(host: str, port: int, timeout: float = 0) -> bool:
        probes.append((host, port))
        return host.startswith("10.8.")

    # First call: LAN fails → silence + tunnel node IP.
    d1 = resolve_ssh_endpoint(
        meta, tcp_open=probe, lan_cooldown_sec=30.0, now=1000.0
    )
    assert d1.path == "tunnel"
    assert d1.host == "10.8.0.2"
    assert "silenced" in d1.detail
    assert probes == [("192.168.5.15", 22), ("10.8.0.2", 22)]

    probes.clear()
    # Still inside silence window: do not touch LAN IP again.
    d2 = resolve_ssh_endpoint(
        meta, tcp_open=probe, lan_cooldown_sec=30.0, now=1020.0
    )
    assert d2.path == "tunnel"
    assert probes == [("10.8.0.2", 22)]

    probes.clear()

    def lan_back(host: str, port: int, timeout: float = 0) -> bool:
        probes.append((host, port))
        return True

    # After cooldown: probe LAN again and prefer it.
    d3 = resolve_ssh_endpoint(
        meta, tcp_open=lan_back, lan_cooldown_sec=30.0, now=1031.0
    )
    assert d3.path == "lan"
    assert d3.host == "192.168.5.15"
    assert probes == [("192.168.5.15", 22)]
    assert not is_lan_silenced("192.168.5.15", 22, now=1031.0)


def test_lan_reprobe_fail_keeps_node_path() -> None:
    """After silence expires, LAN fail only re-silences; Node stays selected."""
    meta = {
        "lan_host": "192.168.5.15",
        "port": 22,
        "tunnel": {
            "enabled": True,
            "node_id": "node-1",
            "rendezvous_url": "http://vps.example:7700",
            "tunnel_host": "10.8.0.2",
            "tunnel_port": 22,
            "path": "tunnel",  # already on Node
        },
    }
    order: list[str] = []

    def probe(host: str, port: int, timeout: float = 0) -> bool:
        order.append(host)
        return host.startswith("10.8.")

    # Silence expired, still on Node: Node probed first, then soft LAN upgrade.
    d = resolve_ssh_endpoint(
        meta, tcp_open=probe, lan_cooldown_sec=30.0, now=2000.0
    )
    assert d.path == "tunnel"
    assert d.host == "10.8.0.2"
    assert order[0] == "10.8.0.2"
    assert "192.168.5.15" in order
    assert is_lan_silenced("192.168.5.15", 22, now=2000.0)


def test_manual_mark_and_clear_lan_failed() -> None:
    mark_lan_failed("10.0.0.1", 22, cooldown_sec=10.0, now=50.0)
    assert is_lan_silenced("10.0.0.1", 22, now=55.0)
    clear_lan_failed("10.0.0.1", 22)
    assert not is_lan_silenced("10.0.0.1", 22, now=55.0)


def test_no_rendezvous_is_explicit_down() -> None:
    meta = {
        "lan_host": "192.168.5.15",
        "port": 22,
        "tunnel": {"enabled": True, "node_id": "node-1"},
    }
    d = resolve_ssh_endpoint(meta, tcp_open=lambda *a, **k: False)
    assert d.path == "down"
    assert d.detail == "tunnel_no_rendezvous"


def test_disabled_tunnel_stays_down_off_lan() -> None:
    meta = {"host": "192.168.1.2", "port": 22}
    d = resolve_ssh_endpoint(meta, tcp_open=lambda *a, **k: False)
    assert d.path == "down"
    assert "not enabled" in d.detail
