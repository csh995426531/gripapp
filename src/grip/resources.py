from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from grip.db import connect, meta_dumps, meta_loads
from grip.redact import assert_no_secrets, strip_forbidden_keys
from grip.vault import Vault

_METRICS_SERIES_MAX = 180  # ~30 min at 10s
_METRICS_SERIES_KEEP = timedelta(minutes=30)


def _parse_iso(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _trim_metrics_series(
    series: list[dict[str, Any]], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - _METRICS_SERIES_KEEP
    kept: list[dict[str, Any]] = []
    for p in series:
        if not isinstance(p, dict):
            continue
        t = _parse_iso(str(p.get("t") or ""))
        if t is None or t >= cutoff:
            kept.append(p)
    if len(kept) > _METRICS_SERIES_MAX:
        kept = kept[-_METRICS_SERIES_MAX:]
    return kept


@dataclass
class Resource:
    name: str
    kind: str
    status: str
    meta: dict[str, Any]
    secret_id: str | None

    def summary(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "labels": strip_forbidden_keys(dict(self.meta)),
        }
        tun = self.meta.get("tunnel")
        if isinstance(tun, dict) and tun.get("enabled"):
            from grip.tunnel.route import route_notice

            payload.update(route_notice(str(tun.get("path") or "down"), when="当前"))
        assert_no_secrets(payload)
        return payload


class ResourceStore:
    def __init__(self, db_path: Path | None = None, vault: Vault | None = None) -> None:
        self._db_path = db_path
        self.vault = vault or Vault()

    def _conn(self):
        return connect(self._db_path)

    def add_ssh(
        self,
        *,
        name: str,
        host: str,
        user: str,
        key_file: Path | None = None,
        password: str | None = None,
        port: int = 22,
        os_name: str = "unix",
    ) -> Resource:
        if not name:
            raise ValueError("name required")
        if bool(key_file) == bool(password):
            raise ValueError("provide exactly one of key_file or password")
        secret_id = f"ssh:{name}"
        if password is not None:
            if password == "":
                raise ValueError("password must not be empty")
            self.vault.put(secret_id, password)
            auth = "password"
        else:
            key_path = Path(key_file).expanduser()  # type: ignore[arg-type]
            if not key_path.is_file():
                raise FileNotFoundError(key_path)
            self.vault.put(secret_id, key_path.read_text(encoding="utf-8"))
            auth = "key"
        meta = {
            "host": host,
            "user": user,
            "port": port,
            "os": (os_name or "unix").lower(),
            "auth": auth,
        }
        assert_no_secrets(meta)
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO resources(name, kind, status, meta_json, secret_id) VALUES (?,?,?,?,?)",
                (name, "ssh", "unknown", meta_dumps(meta), secret_id),
            )
            conn.commit()
        out = self.get(name)
        assert out is not None
        return out

    def update_ssh(
        self,
        name: str,
        *,
        host: str | None = None,
        user: str | None = None,
        port: int | None = None,
        os_name: str | None = None,
        password: str | None = None,
        key_file: Path | None = None,
    ) -> Resource:
        """Update SSH resource fields. Name is immutable. Password/key optional.

        Changing host/port/user without a new password/key is rejected — AutoDL
        (and similar) often reuse the same Grip name while the instance/password
        is new; keeping the old vault secret makes metrics/TCP look fine while
        commands fail with Authentication failed.
        """
        cur = self.get(name)
        if not cur or cur.kind != "ssh":
            raise KeyError(name)
        if password is not None and key_file is not None:
            raise ValueError("provide at most one of password or key_file")
        meta = dict(cur.meta)
        old_host = str(cur.meta.get("host") or "")
        old_user = str(cur.meta.get("user") or "")
        old_port = int(cur.meta.get("port") or 22)
        if host is not None:
            meta["host"] = host.strip()
        if user is not None:
            meta["user"] = user.strip()
        if port is not None:
            meta["port"] = int(port)
        if os_name is not None:
            meta["os"] = (os_name or "unix").lower()
        endpoint_changed = (
            str(meta.get("host") or "") != old_host
            or str(meta.get("user") or "") != old_user
            or int(meta.get("port") or 22) != old_port
        )
        creds_updated = password is not None or key_file is not None
        if endpoint_changed and not creds_updated:
            raise ValueError(
                "endpoint_changed_requires_credentials: "
                "host/user/port changed — enter the new password or private key "
                "(leaving blank would keep the previous machine's secret)"
            )
        secret_id = cur.secret_id or f"ssh:{name}"
        if password is not None:
            if password == "":
                raise ValueError("password must not be empty")
            self.vault.put(secret_id, password)
            meta["auth"] = "password"
        elif key_file is not None:
            key_path = Path(key_file).expanduser()
            if not key_path.is_file():
                raise FileNotFoundError(key_path)
            self.vault.put(secret_id, key_path.read_text(encoding="utf-8"))
            meta["auth"] = "key"
        if endpoint_changed:
            # Drop charts / probe state from the previous instance.
            for k in (
                "metrics",
                "metrics_at",
                "metrics_detail",
                "metrics_series",
                "last_probe_at",
                "last_probe_detail",
            ):
                meta.pop(k, None)
        assert_no_secrets(strip_forbidden_keys(meta))
        with self._conn() as conn:
            conn.execute(
                "UPDATE resources SET meta_json=?, secret_id=? WHERE name=?",
                (meta_dumps(strip_forbidden_keys(meta)), secret_id, name),
            )
            conn.commit()
        out = self.get(name)
        assert out is not None
        return out

    def upsert_ssh_with_key(
        self,
        *,
        name: str,
        host: str,
        user: str,
        key_material: str,
        port: int = 22,
        os_name: str = "unix",
        extra_meta: dict[str, Any] | None = None,
    ) -> Resource:
        """Create/update an SSH resource from in-memory PEM (cloud→SSH link)."""
        if not name or not host or not user:
            raise ValueError("name, host, and user required")
        if not (key_material or "").strip():
            raise ValueError("key_material required")
        secret_id = f"ssh:{name}"
        self.vault.put(secret_id, key_material)
        meta: dict[str, Any] = {
            "host": host.strip(),
            "user": user.strip(),
            "port": int(port),
            "os": (os_name or "unix").lower(),
            "auth": "key",
        }
        if extra_meta:
            meta.update(strip_forbidden_keys(extra_meta))
        assert_no_secrets(strip_forbidden_keys(meta))
        # Preserve status if updating
        cur = self.get(name)
        status = cur.status if cur and cur.kind == "ssh" else "unknown"
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO resources(name, kind, status, meta_json, secret_id) VALUES (?,?,?,?,?)",
                (name, "ssh", status, meta_dumps(strip_forbidden_keys(meta)), secret_id),
            )
            conn.commit()
        out = self.get(name)
        assert out is not None
        return out

    def upsert_cloud_instance(
        self,
        *,
        name: str,
        kind: str,
        status: str,
        meta: dict[str, Any],
    ) -> Resource:
        safe = strip_forbidden_keys(meta)
        assert_no_secrets(safe)
        # Preserve ssh_name / metrics when re-syncing inventory
        cur = self.get(name)
        if cur and cur.kind == kind:
            keep = {
                k: cur.meta[k]
                for k in (
                    "ssh_name",
                    "metrics",
                    "metrics_at",
                    "metrics_series",
                    "metrics_detail",
                )
                if k in cur.meta and k not in safe
            }
            safe = {**keep, **safe}
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO resources(name, kind, status, meta_json, secret_id) VALUES (?,?,?,?,?)",
                (name, kind, status, meta_dumps(safe), None),
            )
            conn.commit()
        out = self.get(name)
        assert out is not None
        return out

    def upsert_oracle_instance(
        self,
        *,
        name: str,
        status: str,
        meta: dict[str, Any],
    ) -> Resource:
        return self.upsert_cloud_instance(
            name=name, kind="oracle_instance", status=status, meta=meta
        )

    def get(self, name: str) -> Resource | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM resources WHERE name=?", (name,)).fetchone()
        if not row:
            return None
        return Resource(
            name=row["name"],
            kind=row["kind"],
            status=row["status"],
            meta=meta_loads(row["meta_json"]),
            secret_id=row["secret_id"],
        )

    def _fetch(self, kind: str | None = None) -> list[Resource]:
        with self._conn() as conn:
            if kind and kind != "all":
                kind_map = {
                    "oracle": "oracle_instance",
                    "gcp": "gcp_instance",
                    "aliyun": "aliyun_instance",
                    "tencent": "tencent_instance",
                    "ssh": "ssh",
                }
                mapped = kind_map.get(kind, kind)
                rows = conn.execute(
                    "SELECT * FROM resources WHERE kind=? ORDER BY name", (mapped,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM resources ORDER BY name").fetchall()
        return [
            Resource(
                name=r["name"],
                kind=r["kind"],
                status=r["status"],
                meta=meta_loads(r["meta_json"]),
                secret_id=r["secret_id"],
            )
            for r in rows
        ]

    def list(self, kind: str | None = None) -> list[Resource]:
        return self._apply_list_order(self._fetch(kind=kind))

    _LIST_ORDER_KEY = "resource_list_order"

    def list_order(self) -> list[str]:
        """User-defined panel order (drag-reorder). Missing names sort by name at end."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM grip_meta WHERE key=?",
                (self._LIST_ORDER_KEY,),
            ).fetchone()
        if not row:
            return []
        try:
            raw = meta_loads(row["value"] if isinstance(row, dict) else row[0])
        except Exception:  # noqa: BLE001
            return []
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        if isinstance(raw, dict):
            return [
                str(x).strip()
                for x in (raw.get("names") or [])
                if str(x).strip()
            ]
        return []

    def set_list_order(self, names: list[str]) -> list[str]:
        """Persist order for known resources; drop unknowns; append any missing."""
        known = {r.name for r in self._fetch()}
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in names or []:
            n = str(raw).strip()
            if not n or n not in known or n in seen:
                continue
            cleaned.append(n)
            seen.add(n)
        for n in sorted(known):
            if n not in seen:
                cleaned.append(n)
                seen.add(n)
        payload = meta_dumps({"names": cleaned})
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO grip_meta(key, value) VALUES (?,?)",
                (self._LIST_ORDER_KEY, payload),
            )
            conn.commit()
        return cleaned

    def _apply_list_order(self, items: list[Resource]) -> list[Resource]:
        if not items:
            return []
        order = self.list_order()
        if not order:
            return items
        by_name = {r.name: r for r in items}
        out: list[Resource] = []
        seen: set[str] = set()
        for n in order:
            r = by_name.get(n)
            if r is None or n in seen:
                continue
            out.append(r)
            seen.add(n)
        rest = sorted(
            (r for r in items if r.name not in seen), key=lambda r: r.name
        )
        out.extend(rest)
        return out

    def _prune_list_order(self, name: str) -> None:
        n = (name or "").strip()
        if not n:
            return
        cur = self.list_order()
        if n not in cur:
            return
        self.set_list_order([x for x in cur if x != n])

    def set_status(self, name: str, status: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE resources SET status=? WHERE name=?", (status, name))
            conn.commit()

    def patch_meta(self, name: str, patch: dict[str, Any]) -> None:
        """Merge meta keys; never touches secret_id."""
        cur = self.get(name)
        if not cur:
            raise KeyError(name)
        meta = dict(cur.meta)
        meta.update(strip_forbidden_keys(patch))
        assert_no_secrets(meta)
        with self._conn() as conn:
            conn.execute(
                "UPDATE resources SET meta_json=? WHERE name=?",
                (meta_dumps(meta), name),
            )
            conn.commit()

    def apply_probe(self, name: str, status: str, detail: str, checked_at: str) -> None:
        self.set_status(name, status)
        self.patch_meta(
            name,
            {"last_probe_at": checked_at, "last_probe_detail": detail},
        )

    def apply_metrics(
        self,
        name: str,
        metrics: dict[str, Any],
        sampled_at: str,
        *,
        detail: str = "ok",
    ) -> None:
        cur = self.get(name)
        if not cur:
            raise KeyError(name)
        series = list(cur.meta.get("metrics_series") or [])
        series.append(
            {
                "t": sampled_at,
                "cpu_used_pct": metrics.get("cpu_used_pct"),
                "load1": metrics.get("load1"),
                "mem_used_pct": metrics.get("mem_used_pct"),
                "disk_used_pct": metrics.get("disk_used_pct"),
                # Cloud vendor path often has throughput instead of disk_used_pct.
                "disk_read_kbps": metrics.get("disk_read_kbps"),
                "disk_write_kbps": metrics.get("disk_write_kbps"),
                "disk_iops_read": metrics.get("disk_iops_read"),
                "disk_iops_write": metrics.get("disk_iops_write"),
                "net_rx_kbps": metrics.get("net_rx_kbps"),
                "net_tx_kbps": metrics.get("net_tx_kbps"),
                "uptime_sec": metrics.get("uptime_sec"),
            }
        )
        series = _trim_metrics_series(series)
        self.patch_meta(
            name,
            {
                "metrics": metrics,
                "metrics_at": sampled_at,
                "metrics_detail": detail,
                "metrics_series": series,
            },
        )

    def delete(self, name: str) -> bool:
        cur = self.get(name)
        if not cur:
            return False
        if cur.secret_id and self.vault.has(cur.secret_id):
            self.vault.delete(cur.secret_id)
        with self._conn() as conn:
            conn.execute("DELETE FROM resources WHERE name=?", (name,))
            conn.commit()
        self._prune_list_order(name)
        return True

    _DISMISSED_KEY = "dismissed_resources"

    def dismissed_names(self) -> set[str]:
        """Cloud inventory names the user deleted — sync must not re-upsert them."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM grip_meta WHERE key=?",
                (self._DISMISSED_KEY,),
            ).fetchone()
        if not row:
            return set()
        try:
            raw = meta_loads(row["value"] if isinstance(row, dict) else row[0])
        except Exception:  # noqa: BLE001
            return set()
        if isinstance(raw, list):
            return {str(x) for x in raw if str(x).strip()}
        if isinstance(raw, dict):
            return {str(x) for x in (raw.get("names") or []) if str(x).strip()}
        return set()

    def dismiss(self, name: str) -> None:
        n = (name or "").strip()
        if not n:
            return
        names = self.dismissed_names()
        if n in names:
            return
        names.add(n)
        payload = meta_dumps({"names": sorted(names)})
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO grip_meta(key, value) VALUES (?,?)",
                (self._DISMISSED_KEY, payload),
            )
            conn.commit()

    def undismiss(self, name: str) -> None:
        n = (name or "").strip()
        if not n:
            return
        names = self.dismissed_names()
        if n not in names:
            return
        names.discard(n)
        payload = meta_dumps({"names": sorted(names)})
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO grip_meta(key, value) VALUES (?,?)",
                (self._DISMISSED_KEY, payload),
            )
            conn.commit()
