from __future__ import annotations

from grip.tunnel.direct import try_wg_direct
from grip.tunnel.rendezvous import reset_state_for_tests, start_rendezvous
from grip.tunnel.store import TunnelStore
from grip.tunnel.wgruntime import OPERATOR_TUNNEL_IP, allocate_tunnel_ip
from grip.tunnel.ops import ensure_tunnel_ready, set_rendezvous_url


def test_allocate_tunnel_ip_skips_operator() -> None:
    assert allocate_tunnel_ip() == "10.77.0.2"
    assert allocate_tunnel_ip({"10.77.0.2", "10.77.0.3"}) == "10.77.0.4"


def test_operator_identity_owns_dot_one(grip_env) -> None:
    store = TunnelStore(root=grip_env / "tunnel")
    ident = store.ensure_identity(role="client")
    assert ident["tunnel_ip"] == OPERATOR_TUNNEL_IP


def test_register_returns_observed_endpoint(grip_env) -> None:
    reset_state_for_tests()
    httpd, url = start_rendezvous(host="127.0.0.1", port=0, background=True)
    try:
        from grip.tunnel.rendezvous import RendezvousClient

        client = RendezvousClient(url)
        resp = client.register(
            {
                "node_id": "node-x",
                "public_key": "x" * 44,
                "role": "node",
                "tunnel_ip": "10.77.0.5",
            }
        )
        assert resp.get("ok") is True
        assert resp.get("observed_addr")
        assert resp.get("suggested_endpoint")
        assert "51820" in str(resp.get("suggested_endpoint"))
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_try_wg_direct_prefers_tunnel(grip_env, monkeypatch) -> None:
    reset_state_for_tests()
    httpd, url = start_rendezvous(host="127.0.0.1", port=0, background=True)
    store = TunnelStore(root=grip_env / "tunnel")
    set_rendezvous_url(url, store)
    try:
        from grip.tunnel.rendezvous import RendezvousClient

        RendezvousClient(url).register(
            {
                "node_id": "node-mini",
                "public_key": "dGVzdC1wdWJrZXktMzJieXRlcyEhISEhISEhISE=",
                "role": "node",
                "tunnel_ip": "10.77.0.9",
                "endpoint": "203.0.113.9:51820",
            }
        )
        monkeypatch.setattr(
            "grip.tunnel.direct.tcp_open",
            lambda host, port, timeout=1.5: host == "10.77.0.9",
        )
        monkeypatch.setattr(
            "grip.tunnel.direct.apply_wg_conf",
            lambda *a, **k: {"ok": True, "action": "mock"},
        )
        meta = {
            "lan_host": "192.168.5.15",
            "port": 22,
            "tunnel": {
                "enabled": True,
                "node_id": "node-mini",
                "public_key": "dGVzdC1wdWJrZXktMzJieXRlcyEhISEhISEhISE=",
                "tunnel_ip": "10.77.0.9",
                "rendezvous_url": url,
            },
        }
        d = try_wg_direct("mini", meta, store=store)
        assert d is not None
        assert d.path == "tunnel"
        assert d.host == "10.77.0.9"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_ensure_prefers_wg_over_relay(grip_env, monkeypatch) -> None:
    reset_state_for_tests()
    httpd, url = start_rendezvous(host="127.0.0.1", port=0, background=True)
    store = TunnelStore(root=grip_env / "tunnel")
    set_rendezvous_url(url, store)
    monkeypatch.setattr("grip.tunnel.ops.tcp_open", lambda *a, **k: False)
    try:
        from grip.tunnel.path import PathDecision

        monkeypatch.setattr(
            "grip.tunnel.direct.try_wg_direct",
            lambda *a, **k: PathDecision(
                path="tunnel", host="10.77.0.9", port=22, detail="wg mock"
            ),
        )
        meta = {
            "lan_host": "192.168.5.15",
            "port": 22,
            "tunnel": {
                "enabled": True,
                "node_id": "node-mini",
                "rendezvous_url": url,
                "model": "node",
            },
        }
        d = ensure_tunnel_ready("mini", meta, store=store)
        assert d.path == "tunnel"
        assert d.host == "10.77.0.9"
    finally:
        httpd.shutdown()
        httpd.server_close()
