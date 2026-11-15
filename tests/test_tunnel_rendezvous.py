from __future__ import annotations

import socket
import threading
import time

from grip.tunnel.rendezvous import RendezvousClient, reset_state_for_tests, start_rendezvous
from grip.tunnel.relay import gateway_dial_relay, open_local_relay_to_rendezvous


def test_register_and_lookup(grip_env) -> None:
    reset_state_for_tests()
    httpd, url = start_rendezvous(host="127.0.0.1", port=0, background=True)
    try:
        c = RendezvousClient(url)
        assert c.health().get("ok") is True
        out = c.register(
            {
                "node_id": "gw-test",
                "public_key": "x" * 44,
                "role": "gateway",
                "endpoint": "1.2.3.4:51820",
                "tunnel_ip": "10.77.0.1",
                "devices": {"mini": {"lan_host": "192.168.5.15", "listen_port": 22000}},
            }
        )
        assert out.get("ok") is True
        peer = c.get_peer("gw-test")["peer"]
        assert peer["endpoint"] == "1.2.3.4:51820"
        assert "mini" in peer["devices"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_tcp_relay_pipes_bytes(grip_env) -> None:
    reset_state_for_tests()
    httpd, url = start_rendezvous(host="127.0.0.1", port=0, background=True)
    lan = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lan.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lan.bind(("127.0.0.1", 0))
    lan.listen(1)
    lan_port = lan.getsockname()[1]
    stop = threading.Event()

    def _echo() -> None:
        lan.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, _ = lan.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            data = conn.recv(1024)
            if data:
                conn.sendall(b"ECHO:" + data)
            conn.close()

    threading.Thread(target=_echo, daemon=True).start()
    local = None
    try:
        session = "sess-relay-1"
        local = open_local_relay_to_rendezvous(
            url, session_id=session, role="client", resource="mini", gateway_id="gw"
        )
        threading.Thread(
            target=lambda: gateway_dial_relay(
                url, session_id=session, lan_host="127.0.0.1", lan_port=lan_port
            ),
            daemon=True,
        ).start()
        deadline = time.time() + 5
        got = b""
        while time.time() < deadline:
            try:
                s = socket.create_connection(
                    (local.listen_host, local.listen_port), timeout=2
                )
                s.sendall(b"ping")
                s.settimeout(3)
                got = s.recv(1024)
                s.close()
                if got:
                    break
            except OSError:
                time.sleep(0.1)
        assert got == b"ECHO:ping"
    finally:
        stop.set()
        if local is not None:
            local.stop.set()
        httpd.shutdown()
        httpd.server_close()
        lan.close()
