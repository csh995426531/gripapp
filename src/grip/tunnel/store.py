from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grip.paths import ensure_home
from grip.tunnel.keys import generate_wg_keypair, new_node_id, public_from_private


def tunnel_root() -> Path:
    root = ensure_home() / "tunnel"
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    return root


class TunnelStore:
    """Filesystem state under ``$GRIP_HOME/tunnel`` (no secrets in git)."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or tunnel_root()
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)

    def _identity_path(self) -> Path:
        return self.root / "identity.json"

    def _gateways_path(self) -> Path:
        return self.root / "gateways.json"

    def _devices_path(self) -> Path:
        return self.root / "devices.json"

    def _settings_path(self) -> Path:
        return self.root / "settings.json"

    def _wg_dir(self) -> Path:
        d = self.root / "wg"
        d.mkdir(parents=True, mode=0o700, exist_ok=True)
        return d

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write_json(self, path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def ensure_identity(self, *, role: str = "client") -> dict[str, Any]:
        from grip.tunnel.wgruntime import OPERATOR_TUNNEL_IP

        cur = self._read_json(self._identity_path(), None)
        if isinstance(cur, dict) and cur.get("private_key") and cur.get("public_key"):
            # Operator / client always owns .1 for WG direct.
            if role in {"client", "operator", "gateway"} and cur.get(
                "tunnel_ip"
            ) != OPERATOR_TUNNEL_IP:
                cur["tunnel_ip"] = OPERATOR_TUNNEL_IP
                if not cur.get("role"):
                    cur["role"] = role
                self._write_json(self._identity_path(), cur)
            return cur
        priv, pub = generate_wg_keypair()
        ident = {
            "node_id": new_node_id(
                "gw" if role == "gateway" else ("op" if role in {"client", "operator"} else "node")
            ),
            "role": role,
            "private_key": priv,
            "public_key": pub,
            "tunnel_ip": OPERATOR_TUNNEL_IP
            if role in {"gateway", "client", "operator"}
            else None,
        }
        self._write_json(self._identity_path(), ident)
        return ident

    def used_tunnel_ips(self) -> set[str]:
        from grip.tunnel.wgruntime import OPERATOR_TUNNEL_IP

        used = {OPERATOR_TUNNEL_IP}
        ident = self.identity()
        if isinstance(ident, dict) and ident.get("tunnel_ip"):
            used.add(str(ident["tunnel_ip"]))
        for v in self.device_map().values():
            if isinstance(v, dict) and v.get("tunnel_ip"):
                used.add(str(v["tunnel_ip"]))
        return used

    def allocate_device_tunnel_ip(self, name: str) -> str:
        from grip.tunnel.wgruntime import allocate_tunnel_ip

        m = self.device_map()
        cur = m.get(name) if isinstance(m.get(name), dict) else None
        if cur and cur.get("tunnel_ip"):
            return str(cur["tunnel_ip"])
        ip = allocate_tunnel_ip(self.used_tunnel_ips())
        m[name] = {
            **(cur or {}),
            "tunnel_ip": ip,
        }
        self.save_device_map(m)
        return ip

    def identity(self) -> dict[str, Any] | None:
        cur = self._read_json(self._identity_path(), None)
        return cur if isinstance(cur, dict) else None

    def settings(self) -> dict[str, Any]:
        cur = self._read_json(self._settings_path(), {})
        return cur if isinstance(cur, dict) else {}

    def set_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        cur = self.settings()
        cur.update(patch)
        self._write_json(self._settings_path(), cur)
        return cur

    def save_gateway_peer(self, peer: dict[str, Any]) -> None:
        data = self._read_json(self._gateways_path(), {})
        if not isinstance(data, dict):
            data = {}
        gid = str(peer.get("node_id") or "")
        if not gid:
            raise ValueError("gateway node_id required")
        data[gid] = peer
        self._write_json(self._gateways_path(), data)

    def gateways(self) -> dict[str, Any]:
        data = self._read_json(self._gateways_path(), {})
        return data if isinstance(data, dict) else {}

    def save_device_map(self, mapping: dict[str, Any]) -> None:
        """resource_name -> {lan_host, lan_port, listen_port}"""
        self._write_json(self._devices_path(), mapping)

    def device_map(self) -> dict[str, Any]:
        data = self._read_json(self._devices_path(), {})
        return data if isinstance(data, dict) else {}

    def upsert_device(
        self,
        name: str,
        *,
        lan_host: str,
        lan_port: int = 22,
        listen_port: int | None = None,
    ) -> dict[str, Any]:
        m = self.device_map()
        used = {int(v.get("listen_port") or 0) for v in m.values() if isinstance(v, dict)}
        port = listen_port
        if port is None:
            port = 22000
            while port in used:
                port += 1
        prev = m.get(name) if isinstance(m.get(name), dict) else {}
        m[name] = {
            **(prev or {}),
            "lan_host": lan_host,
            "lan_port": int(lan_port),
            "listen_port": int(port),
        }
        self.save_device_map(m)
        return m[name]

    def write_wg_conf(self, name: str, body: str) -> Path:
        path = self._wg_dir() / f"{name}.conf"
        path.write_text(body, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def export_public_identity(self) -> dict[str, Any]:
        ident = self.ensure_identity()
        return {
            "node_id": ident["node_id"],
            "role": ident.get("role") or "client",
            "public_key": ident["public_key"]
            if ident.get("public_key")
            else public_from_private(str(ident["private_key"])),
            "tunnel_ip": ident.get("tunnel_ip"),
        }
