from __future__ import annotations

import socket
import threading

import pytest

from grip.service import GripService
from grip.ssh_exec import ExecResult
from grip.tunnel.hosts import is_lan_host
from grip.tunnel.setup import probe_rendezvous_ports, rendezvous_plan, setup_rendezvous
from grip.tunnel.store import TunnelStore
from grip.tunnel.stun import serve_stun_udp, stun_reachable


@pytest.fixture(autouse=True)
def _offline_rendezvous_url(monkeypatch):
    monkeypatch.setattr(
        "grip.tunnel.setup.choose_base_url",
        lambda host, port=443: f"http://{host}:{int(port)}",
    )


def test_exec_result_ok_is_exit_code() -> None:
    assert ExecResult(0, "GRIP_RENDEZVOUS_OK\n", "").ok is True
    assert ExecResult(1, "", "failed").ok is False


def test_is_lan_host_private_ranges() -> None:
    assert is_lan_host("192.168.5.15") is True
    assert is_lan_host("10.1.2.3") is True
    assert is_lan_host("172.16.0.1") is True
    assert is_lan_host("172.31.255.1") is True
    assert is_lan_host("127.0.0.1") is True
    assert is_lan_host("172.32.0.1") is False
    assert is_lan_host("151.145.68.4") is False
    assert is_lan_host("connect.westd.seetacloud.com") is False


def _seed(service: GripService, sample_key) -> None:
    service.resources.add_ssh(
        name="Mac-mini", host="192.168.5.15", user="csh", key_file=sample_key
    )
    service.resources.add_ssh(
        name="Windows主机",
        host="192.168.5.120",
        user="csh",
        key_file=sample_key,
        os_name="windows",
    )
    service.resources.add_ssh(
        name="oci", host="151.145.68.4", user="opc", key_file=sample_key
    )
    service.resources.add_ssh(
        name="autodl-1",
        host="connect.westd.seetacloud.com",
        user="root",
        key_file=sample_key,
    )


def test_plan_offers_prompt_only_when_both_sides_exist(
    service: GripService, sample_key
) -> None:
    _seed(service, sample_key)
    plan = rendezvous_plan(service.resources)
    assert plan["offer"] is True
    assert plan["show_prompt"] is True
    assert [c["name"] for c in plan["candidates"]] == ["autodl-1", "oci"]
    assert plan["lan_devices"] == ["Mac-mini", "Windows主机"]
    assert plan["active"] is None


def test_plan_hides_prompt_after_dismiss_but_keeps_offer(
    service: GripService, sample_key, grip_env
) -> None:
    _seed(service, sample_key)
    store = TunnelStore(root=grip_env / "tunnel")
    store.set_settings({"rendezvous_prompt_dismissed": True})
    plan = rendezvous_plan(service.resources, store=store)
    assert plan["offer"] is True
    assert plan["show_prompt"] is False
    assert plan["dismissed"] is True


def test_plan_idle_when_rendezvous_already_set(
    service: GripService, sample_key, grip_env
) -> None:
    _seed(service, sample_key)
    store = TunnelStore(root=grip_env / "tunnel")
    store.set_settings(
        {
            "rendezvous_url": "http://151.145.68.4:443",
            "rendezvous_host": "oci",
            "rendezvous_token": "secret-token",
        }
    )
    plan = rendezvous_plan(service.resources, store=store)
    assert plan["offer"] is False
    assert plan["show_prompt"] is False
    assert plan["show_retry"] is True
    assert plan["pending_lan"] == ["Mac-mini", "Windows主机"]
    assert plan["active"]["url"] == "http://151.145.68.4:443"
    assert plan["active"]["host_name"] == "oci"
    assert "token" not in plan["active"]
    for lan_name in ("Mac-mini", "Windows主机"):
        service.resources.patch_meta(
            lan_name, {"tunnel": {"enabled": True, "model": "node"}}
        )
    done = rendezvous_plan(service.resources, store=store)
    assert done["show_retry"] is False
    assert done["pending_lan"] == []
    store.set_settings({"rendezvous_prompt_dismissed": True})
    hidden = rendezvous_plan(service.resources, store=store)
    assert hidden["show_retry"] is False


def test_setup_deploys_public_host_and_enrolls_only_lan(
    service: GripService, sample_key, grip_env, monkeypatch
) -> None:
    _seed(service, sample_key)
    store = TunnelStore(root=grip_env / "tunnel")
    calls: list[str] = []

    def fake_ssh(resource, vault, command, **kwargs):
        calls.append(resource.name + "\n" + command)
        return ExecResult(0, "GRIP_RENDEZVOUS_OK\n", "")

    enrolled: list[str] = []

    def fake_enroll(resources, vault, name, **kwargs):
        enrolled.append(name)
        return {"ok": True, "name": name, "model": "node"}

    monkeypatch.setattr("grip.tunnel.setup.run_ssh", fake_ssh)
    monkeypatch.setattr("grip.tunnel.setup.enroll_resource", fake_enroll)
    monkeypatch.setattr("grip.tunnel.setup.probe_rendezvous_ports", lambda *a, **k: [])

    out = setup_rendezvous(
        service.resources, service.vault, "oci", store=store
    )
    assert out["ok"] is True
    assert calls and calls[0].startswith("oci\n")
    assert "443" in calls[0]
    assert "7700" not in calls[0]
    assert enrolled == ["Mac-mini", "Windows主机"]
    settings = store.settings()
    assert settings["rendezvous_url"] == "http://151.145.68.4:443"
    assert settings["rendezvous_host"] == "oci"
    assert settings.get("rendezvous_token")
    assert "rendezvous_token" not in out


def test_board_includes_setup_plan_without_token(
    service: GripService, sample_key, grip_env
) -> None:
    _seed(service, sample_key)
    store = TunnelStore(root=grip_env / "tunnel")
    store.set_settings({"rendezvous_token": "secret-token"})
    board = service.board()
    plan = board["tunnel_setup"]
    assert plan["show_prompt"] is True
    assert "secret-token" not in str(plan)


def test_setup_reuses_live_rendezvous_without_reinstall(
    service: GripService, sample_key, grip_env, monkeypatch
) -> None:
    _seed(service, sample_key)
    service.resources.patch_meta(
        "Mac-mini", {"tunnel": {"enabled": True, "model": "node"}}
    )
    store = TunnelStore(root=grip_env / "tunnel")
    store.set_settings(
        {
            "rendezvous_url": "http://151.145.68.4:443",
            "rendezvous_host": "oci",
            "rendezvous_token": "keep-me",
            "rendezvous_installed": True,
        }
    )

    def boom(*_a, **_k):
        raise AssertionError("should not reinstall rendezvous")

    enrolled: list[str] = []

    def fake_enroll(resources, vault, name, **kwargs):
        enrolled.append(name)
        return {"ok": True, "name": name}

    monkeypatch.setattr("grip.tunnel.setup.run_ssh", boom)
    monkeypatch.setattr("grip.tunnel.setup.enroll_resource", fake_enroll)
    monkeypatch.setattr("grip.tunnel.setup.probe_rendezvous_ports", lambda *a, **k: [])
    out = setup_rendezvous(service.resources, service.vault, "oci", store=store)
    assert out["ok"] is True
    assert out["reused"] is True
    assert enrolled == ["Windows主机"]
    assert store.settings()["rendezvous_token"] == "keep-me"


def test_setup_rejects_lan_host(
    service: GripService, sample_key, grip_env, monkeypatch
) -> None:
    _seed(service, sample_key)
    store = TunnelStore(root=grip_env / "tunnel")
    monkeypatch.setattr(
        "grip.tunnel.setup.run_ssh",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not ssh")),
    )
    out = setup_rendezvous(
        service.resources, service.vault, "Mac-mini", store=store
    )
    assert out["ok"] is False
    assert out["error"] == "not_public_host"


def test_setup_guides_when_public_port_closed(
    service: GripService, sample_key, grip_env, monkeypatch
) -> None:
    _seed(service, sample_key)
    store = TunnelStore(root=grip_env / "tunnel")

    monkeypatch.setattr(
        "grip.tunnel.setup.run_ssh",
        lambda *a, **k: ExecResult(0, "GRIP_RENDEZVOUS_OK\n", ""),
    )
    enrolled: list[str] = []
    monkeypatch.setattr(
        "grip.tunnel.setup.enroll_resource",
        lambda *a, **k: enrolled.append(a[2]) or {"ok": True},
    )
    monkeypatch.setattr(
        "grip.tunnel.setup.probe_rendezvous_ports",
        lambda *a, **k: [
            {"port": 443, "proto": "TCP", "role": "汇合", "reason": "closed"},
        ],
    )
    out = setup_rendezvous(
        service.resources, service.vault, "oci", store=store
    )
    assert out["ok"] is False
    assert out["error"] == "rendezvous_port_closed"
    assert out["ports"] == [443]
    assert out["hint"].startswith("下一步：")
    assert "「oci」（151.145.68.4）" in out["hint"]
    assert "443" in out["hint"]
    assert "7700" not in out["hint"]
    assert "安全组" not in out["hint"]
    assert enrolled == []
    assert store.settings().get("rendezvous_url") in (None, "")
    plan = rendezvous_plan(service.resources, store=store)
    assert plan["show_ports"] is True
    assert plan["show_prompt"] is False
    assert plan["host_name"] == "oci"
    assert plan["port_hint"].startswith("下一步：")
    assert "「oci」（151.145.68.4）" in plan["port_hint"]
    assert "443" in plan["port_hint"]
    assert "安全组" not in plan["port_hint"]
    monkeypatch.setattr(
        "grip.tunnel.setup.run_ssh",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("second probe should not reinstall")),
    )
    again = setup_rendezvous(service.resources, service.vault, "oci", store=store)
    assert again["error"] == "rendezvous_port_closed"


def test_setup_enrolls_when_reach_open_and_names_missing_ports(
    service: GripService, sample_key, grip_env, monkeypatch
) -> None:
    _seed(service, sample_key)
    store = TunnelStore(root=grip_env / "tunnel")
    monkeypatch.setattr(
        "grip.tunnel.setup.run_ssh",
        lambda *a, **k: ExecResult(0, "GRIP_RENDEZVOUS_OK\n", ""),
    )
    enrolled: list[str] = []
    monkeypatch.setattr(
        "grip.tunnel.setup.enroll_resource",
        lambda *a, **k: enrolled.append(a[2]) or {"ok": True},
    )
    monkeypatch.setattr(
        "grip.tunnel.setup.probe_rendezvous_ports",
        lambda *a, **k: [],
    )
    out = setup_rendezvous(service.resources, service.vault, "oci", store=store)
    assert out["ok"] is True
    assert enrolled == ["Mac-mini", "Windows主机"]
    assert "7700" not in out["hint"]
    assert "安全组" not in out["hint"]
    assert store.settings()["rendezvous_url"] == "http://151.145.68.4:443"
    plan = rendezvous_plan(service.resources, store=store)
    assert plan["show_ports"] is False
    assert plan["port_hint"] == ""


def test_setup_skips_reinstall_when_firewall_still_drops(
    service: GripService, sample_key, grip_env, monkeypatch
) -> None:
    _seed(service, sample_key)
    store = TunnelStore(root=grip_env / "tunnel")
    store.set_settings(
        {
            "rendezvous_host": "oci",
            "rendezvous_token": "keep-me",
            "rendezvous_installed": True,
            "rendezvous_port_gaps": [
                {"port": 443, "proto": "TCP", "role": "汇合", "reason": "closed"}
            ],
        }
    )
    monkeypatch.setattr(
        "grip.tunnel.setup.run_ssh",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("firewall drop should not reinstall")),
    )
    monkeypatch.setattr(
        "grip.tunnel.setup.probe_rendezvous_ports",
        lambda *a, **k: [
            {"port": 443, "proto": "TCP", "role": "汇合", "reason": "closed"},
        ],
    )
    out = setup_rendezvous(service.resources, service.vault, "oci", store=store)
    assert out["ok"] is False
    assert out["error"] == "rendezvous_port_closed"
    assert store.settings()["rendezvous_token"] == "keep-me"


def test_setup_restarts_rendezvous_when_reach_port_refused(
    service: GripService, sample_key, grip_env, monkeypatch
) -> None:
    _seed(service, sample_key)
    store = TunnelStore(root=grip_env / "tunnel")
    store.set_settings(
        {
            "rendezvous_host": "oci",
            "rendezvous_token": "keep-me",
            "rendezvous_installed": True,
        }
    )
    calls: list[str] = []
    seen = {"n": 0}

    def fake_ssh(resource, vault, command, **kwargs):
        calls.append(resource.name)
        return ExecResult(0, "GRIP_RENDEZVOUS_OK\n", "")

    def fake_probe(*_a, **_k):
        seen["n"] += 1
        if seen["n"] == 1:
            return [{"port": 443, "proto": "TCP", "role": "汇合", "reason": "refused"}]
        return []

    monkeypatch.setattr("grip.tunnel.setup.run_ssh", fake_ssh)
    monkeypatch.setattr("grip.tunnel.setup.probe_rendezvous_ports", fake_probe)
    monkeypatch.setattr(
        "grip.tunnel.setup.enroll_resource",
        lambda *a, **k: {"ok": True, "name": a[2]},
    )
    out = setup_rendezvous(service.resources, service.vault, "oci", store=store)
    assert calls == ["oci"]
    assert out["ok"] is True
    assert store.settings().get("rendezvous_port_gaps") == []


def test_enroll_failure_tells_which_machine_to_retry() -> None:
    from grip.tunnel.setup import people_enroll_hint

    banner = people_enroll_hint("Windows主机", "Error reading SSH protocol banner")
    assert banner.startswith("下一步：")
    assert "Windows主机" in banner
    assert "banner" not in banner.lower()
    assert "Load failed" not in banner
    slow = people_enroll_hint("Mac-mini", "socket.timeout: timed out")
    assert "Mac-mini" in slow
    assert "没有回应" in slow


def test_tunnel_job_reports_progress_without_waiting(monkeypatch) -> None:
    import threading

    from grip.tunnel.setup import start_tunnel_job, tunnel_job_view

    started = threading.Event()
    release = threading.Event()

    def fake_setup(*_a, **kwargs):
        started.set()
        kwargs["progress"]("正在把隧道程序传到 Windows主机…")
        assert release.wait(2)
        return {"ok": False, "hint": "下一步：点重试，再把隧道程序传到 Windows主机。刚才传到这台机器时超时了。"}

    monkeypatch.setattr("grip.tunnel.setup.setup_rendezvous", fake_setup)
    out = start_tunnel_job(None, None, "oci", reenroll=True)
    assert out["running"] is True
    assert started.wait(1)
    for _ in range(50):
        if "Windows主机" in tunnel_job_view()["hint"]:
            break
        threading.Event().wait(0.02)
    assert "Windows主机" in tunnel_job_view()["hint"]
    release.set()
    for _ in range(50):
        if not tunnel_job_view()["running"]:
            break
        threading.Event().wait(0.02)
    done = tunnel_job_view()
    assert done["running"] is False
    assert "Windows主机" in done["result"]["hint"]


_IFCONFIG = """
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
	inet 192.168.5.49 netmask 0xffffff00 broadcast 192.168.5.255
utun1024: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1500
	inet 198.18.0.1 --> 198.18.0.1 netmask 0xfffffffc
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
	inet 127.0.0.1 netmask 0xff000000
"""

_TUNNEL_ROUTE = """
   route to: 151.145.68.4
destination: 151.145.68.4
    gateway: 198.18.0.1
  interface: utun1024
"""

_DIRECT_ROUTE = """
   route to: 151.145.68.4
destination: 151.145.68.4
    gateway: 192.168.5.1
  interface: en0
"""


def test_physical_ipv4_skips_proxy_tunnel() -> None:
    from grip.tunnel.stun import physical_ipv4_from_ifconfig

    assert physical_ipv4_from_ifconfig(_IFCONFIG) == [("en0", "192.168.5.49")]


def test_route_text_marks_proxy_tunnel() -> None:
    from grip.tunnel.stun import route_text_is_tunnel

    assert route_text_is_tunnel(_TUNNEL_ROUTE) is True
    assert route_text_is_tunnel(_DIRECT_ROUTE) is False


def test_stun_uses_physical_nic_when_tunnel_path_is_silent(monkeypatch) -> None:
    from grip.tunnel import stun as stun_mod

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    stop = threading.Event()
    thread = threading.Thread(
        target=serve_stun_udp,
        args=("127.0.0.1", port),
        kwargs={"stop": stop},
        daemon=True,
    )
    thread.start()
    monkeypatch.setattr(stun_mod, "_route_is_tunnel", lambda _host: True)
    monkeypatch.setattr(stun_mod, "_physical_ipv4", lambda: [("en0", "127.0.0.1")])
    real = stun_mod._stun_exchange

    def via_tunnel_is_silent(host, port, timeout, iface=None, bind_ip=None):
        if iface is None:
            return None
        return real(host, port, timeout, iface=None, bind_ip="127.0.0.1")

    monkeypatch.setattr(stun_mod, "_stun_exchange", via_tunnel_is_silent)
    try:
        assert stun_reachable("127.0.0.1", port, timeout=1.0) is True
    finally:
        stop.set()
        thread.join(timeout=2)


def test_stun_stays_on_direct_route_when_it_is_not_a_tunnel(monkeypatch) -> None:
    from grip.tunnel import stun as stun_mod

    monkeypatch.setattr(stun_mod, "_route_is_tunnel", lambda _host: False)

    def no_physical():
        raise AssertionError("physical nic should not be used")

    monkeypatch.setattr(stun_mod, "_physical_ipv4", no_physical)
    monkeypatch.setattr(stun_mod, "_stun_exchange", lambda *a, **k: None)
    assert stun_reachable("203.0.113.5", 7702, timeout=0.2) is False


def test_stun_reachable_roundtrip() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    stop = threading.Event()
    thread = threading.Thread(
        target=serve_stun_udp,
        args=("127.0.0.1", port),
        kwargs={"stop": stop},
        daemon=True,
    )
    thread.start()
    try:
        assert stun_reachable("127.0.0.1", port, timeout=1.0) is True
    finally:
        stop.set()
        thread.join(timeout=2)


def test_probe_requires_a_real_reply_not_just_tcp_accept() -> None:
    base = _reserve_span(3)
    listeners: list[socket.socket] = []
    stop = threading.Event()
    threads: list[threading.Thread] = []
    for offset in (0, 1):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", base + offset))
        srv.listen(8)
        srv.settimeout(0.2)
        listeners.append(srv)
        threads.append(
            threading.Thread(target=_accept_and_hold, args=(srv, stop), daemon=True)
        )
    for thread in threads:
        thread.start()
    try:
        gaps = probe_rendezvous_ports("127.0.0.1", base, timeout=0.4)
        assert [(item["role"], item["reason"]) for item in gaps] == [
            ("汇合", "closed"),
        ]
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2)
        for srv in listeners:
            srv.close()


def test_probe_reports_fixed_ports_on_localhost() -> None:
    base = _reserve_span(3)
    listeners: list[socket.socket] = []
    stop = threading.Event()
    acceptors: list[threading.Thread] = []
    for offset in (0, 1):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", base + offset))
        srv.listen(8)
        srv.settimeout(0.2)
        listeners.append(srv)
        acceptors.append(
            threading.Thread(
                target=_accept_health if offset == 0 else _accept_relay,
                args=(srv, stop),
                daemon=True,
            )
        )
    for thread in acceptors:
        thread.start()
    stun = threading.Thread(
        target=serve_stun_udp,
        args=("127.0.0.1", base + 2),
        kwargs={"stop": stop},
        daemon=True,
    )
    stun.start()
    try:
        assert probe_rendezvous_ports("127.0.0.1", base, timeout=0.5) == []
        listeners[0].close()
        gaps = probe_rendezvous_ports("127.0.0.1", base, timeout=0.5)
        assert gaps == [
            {"port": base, "proto": "TCP", "role": "汇合", "reason": "refused"}
        ]
    finally:
        stop.set()
        stun.join(timeout=2)
        for thread in acceptors:
            thread.join(timeout=2)
        for srv in listeners:
            srv.close()


def _accept_and_hold(srv: socket.socket, stop: threading.Event) -> None:
    held: list[socket.socket] = []
    while not stop.is_set():
        try:
            conn, _addr = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        held.append(conn)
    for conn in held:
        try:
            conn.close()
        except OSError:
            pass


def _accept_health(srv: socket.socket, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            conn, _addr = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        try:
            conn.settimeout(0.5)
            conn.recv(1024)
            conn.sendall(b'HTTP/1.0 200 OK\r\nContent-Length: 11\r\n\r\n{"ok":true}')
        except OSError:
            pass
        conn.close()


def _accept_relay(srv: socket.socket, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            conn, _addr = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        try:
            conn.settimeout(0.5)
            conn.recv(128)
        except OSError:
            pass
        conn.close()


def _reserve_span(count: int) -> int:
    for base in range(28100, 28900):
        held: list[socket.socket] = []
        try:
            for offset in range(count):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(("127.0.0.1", base + offset))
                held.append(sock)
        except OSError:
            for sock in held:
                sock.close()
            continue
        for sock in held:
            sock.close()
        return base
    raise RuntimeError("no free port span")
