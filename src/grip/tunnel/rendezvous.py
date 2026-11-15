from __future__ import annotations

"""Minimal self-hosted rendezvous: peer register/lookup + TCP relay + STUN.

Run on a machine the user controls (home DDNS or VPS):
  grip tunnel rendezvous --bind 0.0.0.0:443 --token SECRET
  grip tunnel rendezvous --bind 0.0.0.0:443 --tls-cert c.pem --tls-key k.pem
"""

import json
import secrets
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RendezvousState:
    peers: dict[str, dict[str, Any]] = field(default_factory=dict)
    relay_wait: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    auth_token: str = ""
    stun_port: int = 0


STATE = RendezvousState()


def reset_state_for_tests() -> None:
    """Clear in-memory peers/relay waits (unit tests only)."""
    with STATE.lock:
        STATE.peers.clear()
        STATE.relay_wait.clear()
        STATE.auth_token = ""
        STATE.stun_port = 0


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_token(handler: BaseHTTPRequestHandler, data: dict | None = None) -> str:
    auth = str(handler.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    hdr = str(handler.headers.get("X-Grip-Token") or "").strip()
    if hdr:
        return hdr
    if isinstance(data, dict):
        return str(data.get("token") or "").strip()
    return ""


def _require_auth(handler: BaseHTTPRequestHandler, data: dict | None = None) -> bool:
    expected = (STATE.auth_token or "").strip()
    if not expected:
        return True
    got = _extract_token(handler, data)
    if secrets.compare_digest(got, expected):
        return True
    _json_response(handler, 401, {"error": "unauthorized"})
    return False


class RendezvousHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return _json_response(
                self,
                200,
                {
                    "ok": True,
                    "ts": _utc_now(),
                    "auth_required": bool(STATE.auth_token),
                    "stun_port": STATE.stun_port or None,
                    "tls": self.connection.__class__.__module__.startswith("ssl")
                    if self.connection
                    else False,
                },
            )
        if not _require_auth(self):
            return
        if parsed.path == "/v1/whoami":
            host = ""
            try:
                host = str(self.client_address[0] or "")
            except Exception:
                host = ""
            # Keep this file runnable on a host that only has the copied module
            # (no full Grip install). 51820 is the operator WireGuard listen port.
            wg_listen_port = 51820

            return _json_response(
                self,
                200,
                {
                    "observed_addr": host or None,
                    "suggested_endpoint": f"{host}:{wg_listen_port}" if host else None,
                },
            )
        if parsed.path.startswith("/v1/peers/"):
            node_id = parsed.path.rsplit("/", 1)[-1]
            with STATE.lock:
                peer = STATE.peers.get(node_id)
            if not peer:
                return _json_response(self, 404, {"error": "peer_not_found"})
            return _json_response(self, 200, {"peer": peer})
        if parsed.path == "/v1/peers":
            with STATE.lock:
                peers = dict(STATE.peers)
            return _json_response(self, 200, {"peers": peers})
        return _json_response(self, 404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        data = _read_json(self)
        if parsed.path in {"/v1/register", "/v1/relay/open"} and not _require_auth(
            self, data
        ):
            return
        if parsed.path == "/v1/register":
            node_id = str(data.get("node_id") or "").strip()
            if not node_id:
                return _json_response(self, 400, {"error": "node_id_required"})
            observed_host = ""
            try:
                observed_host = str(self.client_address[0] or "")
            except Exception:
                observed_host = ""
            endpoint = data.get("endpoint")
            if not endpoint and observed_host:
                # 51820 is the operator WireGuard listen port. Do not import
                # wgruntime here: the copied rendezvous host does not have it,
                # and that import dropped the register request.
                endpoint = f"{observed_host}:51820"
            peer = {
                "node_id": node_id,
                "public_key": data.get("public_key"),
                "role": data.get("role") or "node",
                "endpoint": endpoint,
                "tunnel_ip": data.get("tunnel_ip"),
                "devices": data.get("devices") or {},
                "updated_at": _utc_now(),
                "observed_addr": observed_host or None,
            }
            with STATE.lock:
                STATE.peers[node_id] = peer
            return _json_response(
                self,
                200,
                {
                    "ok": True,
                    "peer": peer,
                    "observed_addr": observed_host or None,
                    "suggested_endpoint": endpoint,
                },
            )
        if parsed.path == "/v1/relay/open":
            session = str(data.get("session_id") or "").strip()
            if not session:
                return _json_response(self, 400, {"error": "session_id_required"})
            with STATE.lock:
                STATE.relay_wait.setdefault(
                    session,
                    {
                        "meta": {
                            "resource": data.get("resource"),
                            "gateway_id": data.get("gateway_id"),
                            "created_at": _utc_now(),
                        },
                        "slots": [],
                    },
                )
            return _json_response(self, 200, {"ok": True, "session_id": session})
        return _json_response(self, 404, {"error": "not_found"})


def _make_ssl_context(
    *,
    certfile: str | None,
    keyfile: str | None,
    self_signed: bool,
) -> ssl.SSLContext | None:
    if not certfile and not keyfile and not self_signed:
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    if self_signed and not certfile:
        # Generate ephemeral self-signed cert in memory via tempfile.
        import datetime as dt
        import tempfile
        from pathlib import Path

        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.x509.oid import NameOID
        except ImportError as exc:
            raise RuntimeError("cryptography_required_for_self_signed_tls") from exc

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "grip-rendezvous")]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1))
            .not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=825))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        td = Path(tempfile.mkdtemp(prefix="grip-rv-tls-"))
        cert_path = td / "cert.pem"
        key_path = td / "key.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        ctx.load_cert_chain(str(cert_path), str(key_path))
        return ctx
    if not certfile or not keyfile:
        raise ValueError("tls_cert_and_key_required")
    ctx.load_cert_chain(certfile, keyfile)
    return ctx


class _RendezvousServer(ThreadingHTTPServer):
    """HTTP on 443, and the relay handshake on that same socket."""

    def process_request(self, request: Any, client_address: Any) -> None:  # noqa: ANN401
        import socket

        peeked = b""
        try:
            request.settimeout(3)
            peeked = request.recv(16, socket.MSG_PEEK)
        except OSError:
            peeked = b""
        try:
            request.settimeout(None)
        except OSError:
            pass
        if peeked.startswith(b"POST /v1/relay ") or peeked.upper().startswith(b"RELAY"):
            threading.Thread(
                target=_handle_relay_conn, args=(request,), daemon=True
            ).start()
            return
        super().process_request(request, client_address)


def start_rendezvous(
    host: str = "127.0.0.1",
    port: int = 443,
    *,
    background: bool = True,
    token: str | None = None,
    tls_cert: str | None = None,
    tls_key: str | None = None,
    tls_self_signed: bool = False,
    stun_port: int | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    STATE.auth_token = (token or "").strip()
    httpd = _RendezvousServer((host, int(port)), RendezvousHandler)
    ssl_ctx = _make_ssl_context(
        certfile=tls_cert, keyfile=tls_key, self_signed=tls_self_signed
    )
    scheme = "http"
    if ssl_ctx is not None:
        httpd.socket = ssl_ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    bound_host, bound_port = httpd.server_address[:2]
    # Relay is the HTTP upgrade on this same TCP port. UDP direct uses the
    # public port number (443) even when nginx owns TCP 443.
    stun = int(bound_port if stun_port is None else stun_port)
    stun_host = "0.0.0.0" if stun != int(bound_port) else bound_host
    STATE.stun_port = stun
    from grip.tunnel.stun import serve_stun_udp

    threading.Thread(
        target=serve_stun_udp, args=(stun_host, stun), daemon=True
    ).start()
    url = f"{scheme}://{bound_host}:{int(bound_port)}"
    with STATE.lock:
        STATE.peers["_server"] = {
            "relay_tcp_port": int(bound_port),
            "stun_udp_port": stun,
            "http_port": int(bound_port),
            "auth_required": bool(STATE.auth_token),
            "tls": scheme == "https",
        }
    if background:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, url


def _serve_relay_tcp(host: str, port: int) -> None:
    import socket

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, int(port)))
    srv.listen(50)
    while True:
        try:
            conn, _addr = srv.accept()
        except OSError:
            break
        threading.Thread(target=_handle_relay_conn, args=(conn,), daemon=True).start()


def _relay_headers(head: bytes) -> dict[str, str]:
    found: dict[str, str] = {}
    for raw in head.split(b"\r\n")[1:]:
        if b":" not in raw:
            continue
        key, _, val = raw.partition(b":")
        found[key.decode("latin1", errors="replace").strip().lower()] = val.decode(
            "latin1", errors="replace"
        ).strip()
    return found


def _handle_relay_conn(conn: Any) -> None:
    """Pair two peers on the rendezvous port.

    Accepts ``POST /v1/relay`` (so an existing web server on 443 can proxy it)
    and the older ``RELAY`` line.
    """
    conn.settimeout(30)
    try:
        buf = b""
        while b"\n" not in buf and len(buf) < 512:
            chunk = conn.recv(256)
            if not chunk:
                conn.close()
                return
            buf += chunk
        if buf.upper().startswith(b"RELAY"):
            line, _, rest = buf.partition(b"\n")
            parts = line.decode("utf-8", errors="replace").strip().split()
            if len(parts) < 3 or parts[0].upper() != "RELAY":
                conn.close()
                return
            session_id, role = parts[1], parts[2].lower()
            token = parts[3] if len(parts) >= 4 else ""
        elif buf.startswith(b"POST /v1/relay "):
            while b"\r\n\r\n" not in buf and len(buf) < 16384:
                chunk = conn.recv(512)
                if not chunk:
                    conn.close()
                    return
                buf += chunk
            head, _, rest = buf.partition(b"\r\n\r\n")
            headers = _relay_headers(head)
            session_id = headers.get("x-grip-session", "")
            role = headers.get("x-grip-role", "").lower()
            token = headers.get("x-grip-token", "")
            if not session_id or not role:
                conn.close()
                return
            conn.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: grip-relay\r\n"
                b"Connection: Upgrade\r\n\r\n"
            )
        else:
            conn.close()
            return
        expected = (STATE.auth_token or "").strip()
        if expected and not secrets.compare_digest(token, expected):
            try:
                conn.close()
            except OSError:
                pass
            return
        paired = False
        peer_sock = None
        peer_pending = b""
        with STATE.lock:
            slot = STATE.relay_wait.setdefault(
                session_id, {"meta": {}, "slots": [], "paired": False}
            )
            if slot.get("paired"):
                try:
                    conn.close()
                except OSError:
                    pass
                return
            slot["slots"].append({"role": role, "sock": conn, "pending": rest})
            if len(slot["slots"]) >= 2 and not slot.get("paired"):
                slot["paired"] = True
                paired = True
                a = slot["slots"][0]
                b = slot["slots"][1]
                peer_sock = b["sock"] if conn is a["sock"] else a["sock"]
                peer_pending = b["pending"] if conn is a["sock"] else a["pending"]
        if not paired:
            deadline = time.time() + 60
            while time.time() < deadline:
                with STATE.lock:
                    slot = STATE.relay_wait.get(session_id) or {}
                    if slot.get("paired"):
                        return
                time.sleep(0.05)
            try:
                conn.close()
            except OSError:
                pass
            return
        if rest:
            try:
                peer_sock.sendall(rest)
            except OSError:
                pass
        if peer_pending:
            try:
                conn.sendall(peer_pending)
            except OSError:
                pass
        _pipe_sockets(conn, peer_sock)
        with STATE.lock:
            STATE.relay_wait.pop(session_id, None)
    except Exception:
        try:
            conn.close()
        except OSError:
            pass


def _pipe_sockets(a: Any, b: Any) -> None:
    import select

    a.settimeout(None)
    b.settimeout(None)
    sockets = [a, b]
    try:
        while True:
            r, _w, _x = select.select(sockets, [], [], 120)
            if not r:
                break
            for s in r:
                other = b if s is a else a
                data = s.recv(65536)
                if not data:
                    return
                other.sendall(data)
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


# --- client helpers ---


class RendezvousClient:
    def __init__(
        self,
        base_url: str,
        timeout_sec: float = 8.0,
        *,
        token: str | None = None,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout_sec
        self.token = (token or "").strip()

    def _req(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-Grip-Token"] = self.token
        req = Request(self.base + path, data=data, headers=headers, method=method)
        # Allow self-signed in local/dev when scheme is https
        ctx = None
        if self.base.startswith("https://"):
            ctx = ssl.create_default_context()
            # User-owned rendezvous often uses self-signed; keep usable.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            with urlopen(req, timeout=self.timeout, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except TypeError:
            # Older Python without context kw — unlikely on 3.11+
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except Exception:
                return {"error": f"http_{exc.code}", "detail": body[:200]}
        except URLError as exc:
            return {"error": "rendezvous_unreachable", "detail": str(exc.reason)}

    def health(self) -> dict:
        return self._req("GET", "/health")

    def whoami(self) -> dict:
        return self._req("GET", "/v1/whoami")

    def register(self, peer: dict[str, Any]) -> dict:
        return self._req("POST", "/v1/register", peer)

    def get_peer(self, node_id: str) -> dict:
        return self._req("GET", f"/v1/peers/{node_id}")

    def list_peers(self) -> dict:
        return self._req("GET", "/v1/peers")

    def open_relay_session(
        self, session_id: str, *, resource: str = "", gateway_id: str = ""
    ) -> dict:
        return self._req(
            "POST",
            "/v1/relay/open",
            {
                "session_id": session_id,
                "resource": resource,
                "gateway_id": gateway_id,
            },
        )

    def relay_tcp_port(self) -> int:
        parsed = urlparse(self.base)
        return int(parsed.port or (443 if parsed.scheme == "https" else 80))

    def stun_udp_port(self) -> int:
        return self.relay_tcp_port()
