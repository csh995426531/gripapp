from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

from datetime import datetime, timezone

from grip.resources import ResourceStore
from grip.service import GripService
from grip.tunnel.enroll import enroll_resource
from grip.tunnel.node import new_node_config, run_node, write_node_config
from grip.tunnel.ops import (
    confirm_off_lan,
    ensure_tunnel_ready,
    relay_session_id,
    set_rendezvous_url,
    tunnel_status,
)
from grip.tunnel.path import reset_lan_cooldown_for_tests
from grip.tunnel.rendezvous import reset_state_for_tests, start_rendezvous
from grip.tunnel.store import TunnelStore


def test_status_model_is_node(grip_env) -> None:
    store = TunnelStore(root=grip_env / "tunnel")
    set_rendezvous_url("http://127.0.0.1:7700", store)
    st = tunnel_status(store)
    assert st["model"] == "node"
    assert st["rendezvous_url"] == "http://127.0.0.1:7700"


def test_ensure_tunnel_ready_opens_local_relay(grip_env, monkeypatch) -> None:
    reset_state_for_tests()
    reset_lan_cooldown_for_tests()
    monkeypatch.setattr("grip.tunnel.ops.tcp_open", lambda *a, **k: False)
    httpd, url = start_rendezvous(host="127.0.0.1", port=0, background=True)
    store = TunnelStore(root=grip_env / "tunnel")
    set_rendezvous_url(url, store)
    try:
        meta = {
            "lan_host": "192.168.5.15",
            "port": 22,
            "tunnel": {
                "enabled": True,
                "node_id": "node-abc",
                "rendezvous_url": url,
                "model": "node",
            },
        }
        d = ensure_tunnel_ready("mini", meta, store=store)
        assert d.path == "relay"
        assert d.port > 0
        assert d.host == "127.0.0.1"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_ensure_tunnel_ready_keeps_heartbeat(grip_env, monkeypatch) -> None:
    reset_state_for_tests()
    reset_lan_cooldown_for_tests()
    monkeypatch.setattr("grip.tunnel.ops.tcp_open", lambda *a, **k: False)
    httpd, url = start_rendezvous(host="127.0.0.1", port=0, background=True)
    store = TunnelStore(root=grip_env / "tunnel")
    set_rendezvous_url(url, store)
    resources = ResourceStore()
    resources.add_ssh(
        name="mini", host="192.168.5.15", user="csh", password="x", port=22
    )
    proven_at = datetime.now(timezone.utc).isoformat()
    resources.patch_meta(
        "mini",
        {
            "tunnel": {
                "enabled": True,
                "node_id": "node-abc",
                "rendezvous_url": url,
                "model": "node",
                "path": "relay",
                "relay_proven": True,
                "channels": {
                    "relay": {"ok": True, "fail": 0, "at": proven_at},
                },
            }
        },
    )
    stale = {
        "lan_host": "192.168.5.15",
        "port": 22,
        "tunnel": {
            "enabled": True,
            "node_id": "node-abc",
            "rendezvous_url": url,
            "model": "node",
            "relay_proven": False,
            "channels": {"relay": {"ok": False, "fail": 2, "at": proven_at}},
        },
    }
    try:
        ensure_tunnel_ready("mini", stale, store=store, resources=resources)
        tun = (resources.get("mini").meta or {}).get("tunnel") or {}
        assert tun["channels"]["relay"]["ok"] is True
        assert tun["relay_proven"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_node_answers_relay_to_local_echo(grip_env, monkeypatch) -> None:
    # Force LAN probe fail so we exercise relay (autouse fixture opens all TCP).
    monkeypatch.setattr("grip.tunnel.ops.tcp_open", lambda *a, **k: False)
    monkeypatch.setattr("grip.monitor.tcp_open", lambda *a, **k: False)
    reset_state_for_tests()
    reset_lan_cooldown_for_tests()
    httpd, url = start_rendezvous(host="127.0.0.1", port=0, background=True)
    # Local "sshd" echo
    lan = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lan.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lan.bind(("127.0.0.1", 0))
    lan.listen(1)
    lan_port = lan.getsockname()[1]
    stop_echo = threading.Event()

    def _echo() -> None:
        lan.settimeout(0.5)
        while not stop_echo.is_set():
            try:
                c, _ = lan.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            data = c.recv(1024)
            if data:
                c.sendall(b"ECHO:" + data)
            c.close()

    threading.Thread(target=_echo, daemon=True).start()

    cfg = new_node_config(
        rendezvous_url=url, resource_name="mini", ssh_port=lan_port
    )
    cfg["ssh_host"] = "127.0.0.1"
    write_node_config(cfg, grip_env / "node-cfg.json")
    stop_node = threading.Event()
    threading.Thread(
        target=lambda: run_node(cfg, stop=stop_node, poll_interval_sec=0.2),
        daemon=True,
    ).start()
    time.sleep(0.4)

    store = TunnelStore(root=grip_env / "tunnel")
    set_rendezvous_url(url, store)
    meta = {
        "lan_host": "10.255.255.1",
        "port": 22,
        "tunnel": {
            "enabled": True,
            "node_id": cfg["node_id"],
            "rendezvous_url": url,
            "model": "node",
        },
    }
    try:
        d = ensure_tunnel_ready("mini", meta, store=store)
        assert d.path == "relay"
        got = b""
        deadline = time.time() + 6
        while time.time() < deadline:
            try:
                s = socket.create_connection((d.host, d.port), timeout=2)
                s.sendall(b"hi")
                s.settimeout(3)
                got = s.recv(1024)
                s.close()
                if got:
                    break
            except OSError:
                time.sleep(0.15)
        assert got == b"ECHO:hi"
    finally:
        stop_node.set()
        stop_echo.set()
        httpd.shutdown()
        httpd.server_close()
        lan.close()


def test_windows_finish_keeps_success_when_files_were_already_taken() -> None:
    from grip.tunnel.enroll import _windows_finish_script

    script = _windows_finish_script("node-574a87c0")
    assert "node-574a87c0" in script
    assert script.index("GRIP_NODE_OK") < script.index("staged_files_missing")


def test_enroll_commands_fit_ssh_argv() -> None:
    from grip.ssh_exec import _WIN_ARGV_LIMIT, windows_powershell_remote
    from grip.tunnel.enroll import _POSIX_CHUNK, enroll_push_commands

    cfg = {"node_id": "node-1", "rendezvous_url": "http://1.2.3.4:7700"}
    big = b"x" * 250_000
    posix = enroll_push_commands(
        cfg, os_name="macos", remote_wg=b"print(1)\n", wg_gz=big
    )
    assert len(posix) > 3
    assert all(len(cmd) < _POSIX_CHUNK + 800 for cmd in posix[:-1])
    assert "GRIP_NODE_OK" in posix[-1]
    assert "/proc/" not in posix[-1]
    windows = enroll_push_commands(cfg, os_name="windows")
    assert "GRIP_NODE_OK" in windows[-1]
    for cmd in windows:
        assert len(windows_powershell_remote(cmd)) <= _WIN_ARGV_LIMIT


def test_enroll_pushes_node_via_ssh(
    service: GripService, sample_key: Path, grip_env, monkeypatch
) -> None:
    httpd, url = start_rendezvous(host="127.0.0.1", port=0, background=True)
    store = TunnelStore(root=grip_env / "tunnel")
    set_rendezvous_url(url, store)
    service.resources.add_ssh(
        name="mini",
        host="192.168.5.15",
        user="csh",
        key_file=sample_key,
        port=22,
    )

    class FakeResult:
        ok = True
        exit_code = 0
        stdout = "GRIP_NODE_OK node_id=node-x\n"
        stderr = ""

    monkeypatch.setattr("grip.tunnel.enroll.run_ssh", lambda *a, **k: FakeResult())
    try:
        out = enroll_resource(
            service.resources, service.vault, "mini", store=store, rendezvous_url=url
        )
        assert out["ok"] is True
        assert out["model"] == "node"
        r = service.resources.get("mini")
        assert r is not None
        assert r.meta["tunnel"]["enabled"] is True
        assert r.meta["tunnel"]["model"] == "node"
        assert r.meta["tunnel"]["node_id"]
        assert r.meta["tunnel"]["relay_proven"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_relay_session_id_stays_ascii() -> None:
    sid = relay_session_id("node-65de531a")
    sid.encode("ascii")
    assert sid.startswith("node-65de531a:")
    assert "Windows" not in sid


def test_confirm_off_lan_requires_ssh_banner(
    service: GripService, grip_env, monkeypatch
) -> None:
    monkeypatch.setattr("grip.tunnel.ops.tcp_open", lambda *a, **k: False)
    reset_state_for_tests()
    reset_lan_cooldown_for_tests()
    httpd, url = start_rendezvous(host="127.0.0.1", port=0, background=True)
    lan = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lan.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lan.bind(("127.0.0.1", 0))
    lan.listen(1)
    lan_port = lan.getsockname()[1]
    stop_ssh = threading.Event()

    def _banner() -> None:
        lan.settimeout(0.5)
        while not stop_ssh.is_set():
            try:
                client, _ = lan.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                client.sendall(b"SSH-2.0-test\r\n")
            except OSError:
                pass
            finally:
                client.close()

    threading.Thread(target=_banner, daemon=True).start()
    cfg = new_node_config(
        rendezvous_url=url, resource_name="Windows主机", ssh_port=lan_port
    )
    cfg["ssh_host"] = "127.0.0.1"
    stop_node = threading.Event()
    threading.Thread(
        target=lambda: run_node(cfg, stop=stop_node, poll_interval_sec=0.1),
        daemon=True,
    ).start()
    store = TunnelStore(root=grip_env / "tunnel")
    set_rendezvous_url(url, store)
    service.resources.add_ssh(
        name="Windows主机",
        host="192.168.5.120",
        user="csh",
        password="x",
        port=22,
    )
    service.resources.patch_meta(
        "Windows主机",
        {
            "tunnel": {
                "enabled": True,
                "node_id": cfg["node_id"],
                "rendezvous_url": url,
                "model": "node",
            }
        },
    )
    try:
        proved = confirm_off_lan(
            "Windows主机",
            service.resources.get("Windows主机").meta,
            store=store,
            resources=service.resources,
            timeout=4,
        )
        saved = service.resources.get("Windows主机")
        assert proved is True
        assert saved is not None
        assert saved.meta["tunnel"]["relay_proven"] is True
    finally:
        stop_node.set()
        stop_ssh.set()
        httpd.shutdown()
        httpd.server_close()
        lan.close()
