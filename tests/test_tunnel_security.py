from __future__ import annotations

import gzip
import socket
import time

from grip.tunnel.rendezvous import RendezvousClient, reset_state_for_tests, start_rendezvous
from grip.tunnel.stun import MAGIC, discover_endpoint, stun_reply
from grip.tunnel.wgembed import (
    mark_privilege_denied,
    privilege_in_cooldown,
    reset_privilege_cooldown_for_tests,
)


def test_stun_reply_format() -> None:
    reply = stun_reply(MAGIC + b"\n", ("203.0.113.9", 54321))
    assert reply is not None
    assert b"203.0.113.9" in reply
    assert b"54321" in reply


def test_rendezvous_token_auth(grip_env) -> None:
    reset_state_for_tests()
    httpd, url = start_rendezvous(
        host="127.0.0.1", port=0, background=True, token="s3cret"
    )
    try:
        bad = RendezvousClient(url, token="wrong")
        assert bad.register({"node_id": "n1", "public_key": "x"}).get("error") in {
            "unauthorized",
            "http_401",
        } or bad.register({"node_id": "n1", "public_key": "x"}).get("error")
        # health stays open
        assert RendezvousClient(url).health().get("ok") is True
        good = RendezvousClient(url, token="s3cret")
        reg = good.register(
            {
                "node_id": "n1",
                "public_key": "dGVzdC1wdWJrZXktMzJieXRlcyEhISEhISEhISE=",
                "role": "node",
                "tunnel_ip": "10.77.0.2",
            }
        )
        assert reg.get("ok") is True
        peers = good.list_peers()
        assert "n1" in (peers.get("peers") or {})
        who = good.whoami()
        assert who.get("observed_addr")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_stun_udp_via_rendezvous(grip_env) -> None:
    reset_state_for_tests()
    httpd, url = start_rendezvous(host="127.0.0.1", port=0, background=True)
    try:
        # Give STUN thread a moment to bind.
        time.sleep(0.1)
        ep = discover_endpoint(url, listen_port=51820)
        assert ep is not None
        assert ep.endswith(":51820")
        # Localhost observation
        assert ep.startswith("127.0.0.1:") or ep.startswith("::1:")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_privilege_cooldown_persists_across_reset_memory(grip_env) -> None:
    reset_privilege_cooldown_for_tests()
    mark_privilege_denied(cooldown_sec=120.0)
    assert privilege_in_cooldown() is True
    # Simulate process restart: clear memory only, keep disk marker.
    from grip.tunnel import wgembed as wge

    with wge._priv_lock:
        wge._priv_deny_until = 0.0
    assert privilege_in_cooldown() is True
    reset_privilege_cooldown_for_tests()


def test_gzip_shrinks_wireguard_binary() -> None:
    from grip.tunnel.wgembed import bundled_binary_path

    path = bundled_binary_path("linux-amd64")
    assert path is not None
    raw = path.read_bytes()
    gz = gzip.compress(raw, compresslevel=9)
    assert len(gz) < len(raw)
    assert gzip.decompress(gz) == raw
