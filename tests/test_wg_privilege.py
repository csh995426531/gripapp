from __future__ import annotations

import time
from pathlib import Path

from grip.tunnel.keys import generate_wg_keypair
from grip.tunnel.wgconfig import mesh_conf
from grip.tunnel.wgembed import (
    apply_embedded_wg,
    looks_like_privilege_error,
    mark_privilege_denied,
    privilege_in_cooldown,
    privilege_prompts_allowed,
    reset_privilege_cooldown_for_tests,
)
from grip.tunnel.direct import try_wg_direct
from grip.tunnel.store import TunnelStore


def setup_function() -> None:
    reset_privilege_cooldown_for_tests()


def test_privilege_prompts_disabled_by_env(monkeypatch) -> None:
    monkeypatch.delenv("GRIP_WG_PRIV", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("GRIP_WG_PRIV", "0")
    assert privilege_prompts_allowed() is False
    monkeypatch.setenv("GRIP_WG_PRIV", "1")
    assert privilege_prompts_allowed() is True


def test_looks_like_privilege_error_only_on_permission_text() -> None:
    assert looks_like_privilege_error("Failed to create TUN: operation not permitted")
    assert looks_like_privilege_error("User canceled.")
    assert not looks_like_privilege_error("exec format error")
    assert not looks_like_privilege_error("process_exited")
    assert not looks_like_privilege_error("")


def test_admin_dialog_is_branded_as_grip() -> None:
    from grip.tunnel.wgpriv import _ADMIN_PROMPT, darwin_admin_source

    src = darwin_admin_source(
        Path("/tmp/grip-job.sh"),
        Path("/tmp/grip-out.txt"),
        Path("/tmp/grip-err.txt"),
        _ADMIN_PROMPT,
    )
    assert "osascript" not in src
    assert "with administrator privileges" in src
    assert "Grip 需要在这台 Mac 上建立直连网卡" in src
    assert "quoted form of scriptPath" in src


def test_darwin_bootstrap_uses_sys_executable(tmp_path) -> None:
    from grip.tunnel.wgpriv import write_darwin_priv_bootstrap
    import sys

    script = write_darwin_priv_bootstrap(
        binary=tmp_path / "wireguard-go",
        conf_path=tmp_path / "grip0.conf",
        remote_wg_py=tmp_path / "remote_wg.py",
        log_path=tmp_path / "wg.log",
        pid_path=tmp_path / "wg.pid",
        uapi_dir=tmp_path / "uapi",
        python_bin=sys.executable,
    )
    body = script.read_text(encoding="utf-8")
    assert "PY=" in body
    assert sys.executable in body
    assert "/usr/bin/python3 \"$BOOT\"" not in body
    script.unlink()


def test_privilege_cooldown_blocks_default_prompts(monkeypatch) -> None:
    monkeypatch.delenv("GRIP_WG_PRIV", raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert privilege_prompts_allowed() is True
    mark_privilege_denied(cooldown_sec=60.0)
    assert privilege_in_cooldown() is True
    assert privilege_prompts_allowed() is False
    # Explicit override still works.
    monkeypatch.setenv("GRIP_WG_PRIV", "1")
    assert privilege_prompts_allowed() is True
    monkeypatch.delenv("GRIP_WG_PRIV", raising=False)
    # Expired wall-clock marker should clear cooldown.
    from grip.tunnel import wgembed as wge

    wge._priv_deny_path().write_text(f"{time.time() - 10:.3f}\n", encoding="utf-8")
    with wge._priv_lock:
        wge._priv_deny_until = 0.0
    assert privilege_in_cooldown() is False


def test_privilege_failure_marks_cooldown(grip_env, monkeypatch) -> None:
    monkeypatch.setenv("GRIP_WG_PRIV", "1")  # allow prompt path
    store = TunnelStore(root=grip_env / "tunnel")
    priv, _ = generate_wg_keypair()
    _, pub2 = generate_wg_keypair()
    conf = mesh_conf(
        private_key=priv,
        address="10.77.0.1",
        listen_port=51820,
        peers=[
            {
                "public_key": pub2,
                "tunnel_ip": "10.77.0.2",
                "endpoint": "127.0.0.1:51821",
            }
        ],
    )
    path = store.write_wg_conf("grip0", conf)

    monkeypatch.setattr(
        "grip.tunnel.wgembed._apply_unprivileged",
        lambda *a, **k: {
            "ok": False,
            "error": "utun_iface_not_found",
            "detail": "operation not permitted",
            "need_privilege": True,
        },
    )
    monkeypatch.setattr(
        "grip.tunnel.wgembed._apply_darwin_privileged",
        lambda *a, **k: {
            "ok": False,
            "error": "darwin_privilege_failed",
            "detail": "User canceled.",
        },
    )
    monkeypatch.setattr("grip.tunnel.wgembed.platform.system", lambda: "Darwin")

    out = apply_embedded_wg(path, allow_privilege=True)
    assert out.get("ok") is False
    assert out.get("fallback") == "relay"
    assert privilege_in_cooldown() is True
    # Default path must not re-prompt while cooling down.
    monkeypatch.delenv("GRIP_WG_PRIV", raising=False)
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("should not re-prompt")

    monkeypatch.setattr("grip.tunnel.wgembed._apply_darwin_privileged", boom)
    out2 = apply_embedded_wg(path, allow_privilege=None)
    assert out2.get("ok") is False
    assert called["n"] == 0


def test_apply_embedded_fails_honestly_without_priv(grip_env, monkeypatch) -> None:
    monkeypatch.setenv("GRIP_WG_PRIV", "0")
    store = TunnelStore(root=grip_env / "tunnel")
    priv, _ = generate_wg_keypair()
    _, pub2 = generate_wg_keypair()
    conf = mesh_conf(
        private_key=priv,
        address="10.77.0.1",
        listen_port=51820,
        peers=[
            {
                "public_key": pub2,
                "tunnel_ip": "10.77.0.2",
                "endpoint": "127.0.0.1:51821",
            }
        ],
    )
    path = store.write_wg_conf("grip0", conf)
    # On macOS without TUN rights this fails; on CI linux may also fail without /dev/net/tun.
    out = apply_embedded_wg(path, allow_privilege=False)
    assert out.get("ok") is False
    assert out.get("fallback") == "relay" or out.get("error")


def test_try_wg_direct_returns_none_when_apply_fails(grip_env, monkeypatch) -> None:
    monkeypatch.setenv("GRIP_WG_PRIV", "0")
    store = TunnelStore(root=grip_env / "tunnel")
    store.set_settings({"rendezvous_url": "http://127.0.0.1:9"})
    monkeypatch.setattr(
        "grip.tunnel.direct.apply_wg_conf",
        lambda *a, **k: {"ok": False, "error": "wg_apply_failed", "fallback": "relay"},
    )
    monkeypatch.setattr("grip.tunnel.direct.tcp_open", lambda *a, **k: True)

    class _FakeClient:
        def register(self, peer):
            return {"ok": True}

        def get_peer(self, nid):
            return {
                "peer": {
                    "node_id": nid,
                    "public_key": "dGVzdC1wdWJrZXktMzJieXRlcyEhISEhISEhISE=",
                    "tunnel_ip": "10.77.0.9",
                }
            }

    monkeypatch.setattr(
        "grip.tunnel.clientutil.rendezvous_client",
        lambda *a, **k: _FakeClient(),
    )
    monkeypatch.setattr(
        "grip.tunnel.clientutil.resolve_endpoint_hint",
        lambda *a, **k: "203.0.113.1:51820",
    )
    monkeypatch.setattr(
        "grip.tunnel.direct.collect_node_peers",
        lambda *a, **k: [],
    )
    meta = {
        "port": 22,
        "tunnel": {
            "enabled": True,
            "node_id": "node-x",
            "public_key": "dGVzdC1wdWJrZXktMzJieXRlcyEhISEhISEhISE=",
            "tunnel_ip": "10.77.0.9",
            "rendezvous_url": "http://127.0.0.1:9",
        },
    }
    d = try_wg_direct("mini", meta, store=store)
    assert d is None
