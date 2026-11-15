from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from grip.audit_integrity import Integrity, append_chained_event, verify_chain
from grip.db import connect
from grip.redact import assert_no_secrets, redact_text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_actor(actor: str | None, *, default: str = "unknown") -> str:
    """Sanitize actor label for storage (panel|mcp|cli|unknown|custom)."""
    raw = (actor or "").strip()
    if not raw:
        return default
    cleaned = redact_text(raw).replace("\n", " ").replace("\r", " ").strip()
    if not cleaned:
        return default
    return cleaned[:128]


@dataclass
class Task:
    id: str
    resource_name: str
    action: str
    status: str
    created_at: str
    finished_at: str | None = None
    exit_code: int | None = None
    summary: str | None = None
    error: str | None = None
    command: str | None = None
    last_heartbeat_at: str | None = None
    phase: str | None = None
    probe_json: str | None = None
    actor: str = "unknown"

    def summary_dto(self) -> dict:
        probe = None
        if self.probe_json:
            try:
                probe = json.loads(self.probe_json)
            except json.JSONDecodeError:
                probe = None
        payload = {
            "id": self.id,
            "resource_name": self.resource_name,
            "action": self.action,
            "status": self.status,
            "phase": self.phase or self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "exit_code": self.exit_code,
            "summary": redact_text(self.summary),
            "error": redact_text(self.error) if self.error else None,
            "await_probe": probe,
            "actor": self.actor or "unknown",
            "command": redact_text(self.command) if self.command else None,
        }
        assert_no_secrets(payload)
        return payload


class TaskStore:
    def __init__(
        self,
        db_path: Path | None = None,
        *,
        hmac_key_provider: Callable[[], bytes] | None = None,
    ) -> None:
        self._db_path = db_path
        self._hmac_key_provider = hmac_key_provider

    def _conn(self):
        return connect(self._db_path)

    def _hmac_key(self) -> bytes | None:
        if self._hmac_key_provider is None:
            return None
        try:
            return self._hmac_key_provider()
        except Exception:  # noqa: BLE001
            return None

    def add_event(self, task_id: str, kind: str, detail: str | None = None) -> None:
        at = _now()
        detail_r = redact_text(detail) if detail else None
        key = self._hmac_key()
        with self._conn() as conn:
            if key is not None:
                append_chained_event(
                    conn,
                    key,
                    task_id=task_id,
                    at=at,
                    kind=kind,
                    detail=detail_r,
                )
            else:
                conn.execute(
                    "INSERT INTO task_events(task_id, at, kind, detail) VALUES (?,?,?,?)",
                    (task_id, at, kind, detail_r),
                )
            conn.commit()

    def audit_integrity(self) -> Integrity:
        key = self._hmac_key()
        if key is None:
            return "unknown"
        with self._conn() as conn:
            return verify_chain(conn, key)

    def events(self, task_id: str, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT at, kind, detail FROM task_events
                   WHERE task_id=? ORDER BY id DESC LIMIT ?""",
                (task_id, limit),
            ).fetchall()
        return [
            {"at": r["at"], "kind": r["kind"], "detail": r["detail"]} for r in rows
        ]

    def create(
        self,
        *,
        resource_name: str,
        action: str,
        command: str | None = None,
        status: str = "queued",
        probe: dict | None = None,
        actor: str | None = None,
    ) -> Task:
        actor_n = normalize_actor(actor, default="unknown")
        task = Task(
            id=str(uuid.uuid4()),
            resource_name=resource_name,
            action=action,
            status=status,
            created_at=_now(),
            command=command,
            phase="queued",
            probe_json=json.dumps(probe) if probe else None,
            actor=actor_n,
        )
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO tasks(
                     id, resource_name, action, status, created_at, command,
                     phase, probe_json, last_heartbeat_at, actor
                   ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    task.id,
                    task.resource_name,
                    task.action,
                    task.status,
                    task.created_at,
                    task.command,
                    task.phase,
                    task.probe_json,
                    None,
                    task.actor,
                ),
            )
            conn.commit()
        self.add_event(task.id, "created", f"{action} actor={actor_n}")
        return task

    def set_running(self, task_id: str) -> None:
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """UPDATE tasks SET status=?, phase=?, last_heartbeat_at=? WHERE id=?""",
                ("running", "session", now, task_id),
            )
            conn.commit()
        self.add_event(task_id, "running", "executor session started")

    def set_orphaned(self, task_id: str, detail: str | None = None) -> None:
        """Started on remote but Grip lost the wait session — reconcile later."""
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """UPDATE tasks SET status=?, phase=?, last_heartbeat_at=? WHERE id=?
                   AND status IN ('running','probing','queued')""",
                ("orphaned", "reconcile", now, task_id),
            )
            conn.commit()
        self.add_event(task_id, "orphaned", detail or "ssh_lost_after_start")

    def heartbeat(self, task_id: str) -> None:
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET last_heartbeat_at=? WHERE id=? AND status IN ('running','probing')",
                (now, task_id),
            )
            conn.commit()

    def list_orphaned(self, *, name: str | None = None, limit: int = 50) -> list[Task]:
        limit = max(1, min(int(limit), 200))
        with self._conn() as conn:
            if name:
                rows = conn.execute(
                    """SELECT * FROM tasks WHERE status='orphaned' AND resource_name=?
                       ORDER BY created_at DESC LIMIT ?""",
                    (name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM tasks WHERE status='orphaned'
                       ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [self._row(r) for r in rows]

    def reap_stale_running(self, *, max_age_sec: float = 90.0) -> list[str]:
        """Finish running/probing tasks whose heartbeat (or created_at) is too old.

        Covers dead worker threads that never call finish — UI would otherwise
        show 执行中 forever. Default 90s ≈ default timeout 60s + 30s grace.
        Does not reap orphaned tasks (those await remote reconcile).
        """
        now = datetime.now(timezone.utc)
        candidates = self.list(limit=100, status="running") + self.list(
            limit=100, status="probing"
        )
        reaped: list[str] = []
        for t in candidates:
            stamp = t.last_heartbeat_at or t.created_at
            if not stamp:
                continue
            try:
                dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (now - dt).total_seconds()
            if age <= float(max_age_sec):
                continue
            try:
                self.finish(
                    t.id,
                    status="failed",
                    error="stale_timeout",
                    summary=t.summary,
                )
            except KeyError:
                continue
            reaped.append(t.id)
        return reaped

    def set_probing(self, task_id: str, detail: str | None = None) -> None:
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """UPDATE tasks SET status=?, phase=?, last_heartbeat_at=? WHERE id=?""",
                ("probing", "probing", now, task_id),
            )
            conn.commit()
        self.add_event(task_id, "probing", detail)

    def finish(
        self,
        task_id: str,
        *,
        status: str,
        exit_code: int | None = None,
        summary: str | None = None,
        error: str | None = None,
    ) -> Task:
        finished = _now()
        summary_r = redact_text(summary)
        error_r = redact_text(error) if error else None
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE tasks SET status=?, phase=?, finished_at=?, exit_code=?,
                   summary=?, error=?, last_heartbeat_at=?
                   WHERE id=?""",
                (
                    status,
                    "done",
                    finished,
                    exit_code,
                    summary_r,
                    error_r,
                    finished,
                    task_id,
                ),
            )
            if cur.rowcount == 0:
                raise KeyError(task_id)
            conn.commit()
        self.add_event(task_id, status, error_r or summary_r)
        task = self.get(task_id)
        assert task is not None
        return task

    def get(self, task_id: str) -> Task | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        return self._row(row)

    def list(self, *, limit: int = 20, status: str | None = None) -> list[Task]:
        limit = max(1, min(int(limit), 100))
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row(r) for r in rows]

    def list_audit(
        self,
        *,
        name: str | None = None,
        limit: int = 2000,
        action: str | None = None,
    ) -> list[Task]:
        """Recent tasks for audit export (higher limit than list())."""
        limit = max(1, min(int(limit), 10000))
        clauses: list[str] = []
        params: list[object] = []
        if name:
            clauses.append("resource_name=?")
            params.append(name)
        if action:
            clauses.append("action=?")
            params.append(action)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM tasks{where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row) -> Task:
        keys = row.keys()
        actor = "unknown"
        if "actor" in keys and row["actor"]:
            actor = row["actor"]
        return Task(
            id=row["id"],
            resource_name=row["resource_name"],
            action=row["action"],
            status=row["status"],
            created_at=row["created_at"],
            finished_at=row["finished_at"],
            exit_code=row["exit_code"],
            summary=row["summary"],
            error=row["error"],
            command=row["command"],
            last_heartbeat_at=row["last_heartbeat_at"]
            if "last_heartbeat_at" in keys
            else None,
            phase=row["phase"] if "phase" in keys else None,
            probe_json=row["probe_json"] if "probe_json" in keys else None,
            actor=actor,
        )
