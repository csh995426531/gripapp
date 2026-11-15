from __future__ import annotations

"""Long-lived SSH sessions for reuse (metrics / frequent exec).

Password auth: cached paramiko clients (exec_command per call).
Key auth: OpenSSH ControlMaster multiplex (ControlPersist).
"""

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from grip.paths import grip_home
from grip.resources import Resource
from grip.vault import Vault

_lock = threading.Lock()
# resource.name -> (paramiko.SSHClient, last_used_monotonic)
_paramiko_clients: dict[str, tuple[object, float]] = {}
_IDLE_SEC = float(os.environ.get("GRIP_SSH_SESSION_IDLE_SEC") or "180")
_CONNECT_RETRIES = max(1, int(os.environ.get("GRIP_SSH_CONNECT_RETRIES") or "3"))


def _lock_dir() -> Path:
    d = grip_home() / "ssh-locks"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


@contextmanager
def _cross_process_connect_lock(name: str) -> Iterator[None]:
    """Serialize password handshakes across menubar + MCP processes.

    AutoDL / seetacloud gateways often return Authentication failed when two
    processes log in at once; in-process semaphores cannot see the other PID.
    """
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in (name or "_"))
    path = _lock_dir() / f"{safe}.connect.lock"
    fh = open(path, "a+", encoding="utf-8")
    try:
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        try:
            fh.close()
        except OSError:
            pass



def mux_dir() -> Path:
    d = grip_home() / "ssh-mux"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def control_path_for(resource: Resource) -> str:
    """OpenSSH ControlPath for key-based multiplex."""
    host = str(resource.meta.get("host") or "h")
    user = str(resource.meta.get("user") or "u")
    port = int(resource.meta.get("port") or 22)
    # Keep path short/safe for OpenSSH.
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in f"{user}_{host}_{port}")
    return str(mux_dir() / f"{safe}.sock")


def key_ssh_mux_opts(resource: Resource) -> list[str]:
    """Extra -o flags for ssh(1) ControlMaster reuse."""
    path = control_path_for(resource)
    persist = str(int(os.environ.get("GRIP_SSH_CONTROL_PERSIST") or "120"))
    return [
        "-o",
        "ControlMaster=auto",
        "-o",
        f"ControlPath={path}",
        "-o",
        f"ControlPersist={persist}",
    ]


def _drop_paramiko(name: str) -> None:
    ent = _paramiko_clients.pop(name, None)
    if not ent:
        return
    client, _ = ent
    try:
        client.close()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def close_all_sessions() -> None:
    with _lock:
        names = list(_paramiko_clients.keys())
        for n in names:
            _drop_paramiko(n)


def get_paramiko_client(
    resource: Resource,
    vault: Vault,
    *,
    connect_timeout_sec: int,
) -> object:
    """Return a connected paramiko SSHClient, reusing when possible."""
    import paramiko

    name = resource.name
    password = vault.get(resource.secret_id)  # type: ignore[arg-type]
    host = resource.meta["host"]
    user = resource.meta["user"]
    port = int(resource.meta.get("port") or 22)
    now = time.monotonic()

    with _lock:
        # Idle eviction
        stale = [
            n
            for n, (_, used) in _paramiko_clients.items()
            if now - used > _IDLE_SEC
        ]
        for n in stale:
            _drop_paramiko(n)

        ent = _paramiko_clients.get(name)
        if ent is not None:
            client, _ = ent
            transport = client.get_transport()  # type: ignore[attr-defined]
            if transport is not None and transport.is_active():
                _paramiko_clients[name] = (client, now)
                return client
            _drop_paramiko(name)

    # New handshake — lock across processes, retry auth flakes.
    last_exc: Exception | None = None
    with _cross_process_connect_lock(name):
        # Another process may have raced; re-check local cache once under lock.
        with _lock:
            ent = _paramiko_clients.get(name)
            if ent is not None:
                client, _ = ent
                transport = client.get_transport()  # type: ignore[attr-defined]
                if transport is not None and transport.is_active():
                    _paramiko_clients[name] = (client, time.monotonic())
                    return client
                _drop_paramiko(name)

        for attempt in range(_CONNECT_RETRIES):
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(
                    hostname=host,
                    port=port,
                    username=user,
                    password=password,
                    timeout=connect_timeout_sec,
                    allow_agent=False,
                    look_for_keys=False,
                )
                with _lock:
                    _paramiko_clients[name] = (client, time.monotonic())
                return client
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
                msg = str(exc).lower()
                authish = (
                    "authentication" in msg
                    or "auth fail" in msg
                    or "permission denied" in msg
                )
                if authish and attempt + 1 < _CONNECT_RETRIES:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise
    raise RuntimeError(str(last_exc) if last_exc else "ssh_connect_failed")


def invalidate_paramiko(resource_name: str) -> None:
    with _lock:
        _drop_paramiko(resource_name)
