from __future__ import annotations

"""Tamper-evident hash chain over task_events.

Detects quiet wipe/truncate of the audit trail. Does not prevent a same-user
agent from deleting files — it makes that visible on board.security.
"""

import hashlib
import hmac
import sqlite3
from typing import Literal

GENESIS = "0" * 64
META_HEAD = "audit_head_hash"
Integrity = Literal["ok", "broken", "unknown"]


def event_mac(
    key: bytes,
    *,
    prev_hash: str,
    task_id: str,
    at: str,
    kind: str,
    detail: str | None,
) -> str:
    msg = f"{prev_hash}\n{task_id}\n{at}\n{kind}\n{detail or ''}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM grip_meta WHERE key=?", (key,)
    ).fetchone()
    if not row:
        return None
    return str(row["value"] if isinstance(row, sqlite3.Row) else row[0])


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO grip_meta(key, value) VALUES (?,?)",
        (key, value),
    )


def append_chained_event(
    conn: sqlite3.Connection,
    key: bytes,
    *,
    task_id: str,
    at: str,
    kind: str,
    detail: str | None,
) -> str:
    """Insert event with HMAC chain link; update sealed head. Returns event_hash."""
    head = get_meta(conn, META_HEAD)
    if head:
        prev = head
    else:
        # Continue from last hashed row if any (upgrade path)
        last = conn.execute(
            """SELECT event_hash FROM task_events
               WHERE event_hash IS NOT NULL AND event_hash != ''
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        prev = (
            str(last["event_hash"] if isinstance(last, sqlite3.Row) else last[0])
            if last
            else GENESIS
        )
    digest = event_mac(
        key,
        prev_hash=prev,
        task_id=task_id,
        at=at,
        kind=kind,
        detail=detail,
    )
    conn.execute(
        """INSERT INTO task_events(task_id, at, kind, detail, prev_hash, event_hash)
           VALUES (?,?,?,?,?,?)""",
        (task_id, at, kind, detail, prev, digest),
    )
    set_meta(conn, META_HEAD, digest)
    return digest


def verify_chain(conn: sqlite3.Connection, key: bytes) -> Integrity:
    """Verify all hashed events and sealed head pointer."""
    rows = conn.execute(
        """SELECT id, task_id, at, kind, detail, prev_hash, event_hash
           FROM task_events
           WHERE event_hash IS NOT NULL AND event_hash != ''
           ORDER BY id ASC"""
    ).fetchall()
    head = get_meta(conn, META_HEAD)

    if not rows:
        if head:
            return "broken"  # head without events = wipe
        return "ok"

    expected_prev = GENESIS
    # If the first hashed row continues an older unhashed history, allow its
    # recorded prev_hash as the start (cannot re-verify pre-chain rows).
    first = rows[0]
    first_prev = first["prev_hash"] if isinstance(first, sqlite3.Row) else first[5]
    if first_prev and first_prev != GENESIS:
        # Accept recorded prev only for the first hashed event (legacy bridge).
        expected_prev = str(first_prev)

    last_hash = ""
    for r in rows:
        task_id = r["task_id"] if isinstance(r, sqlite3.Row) else r[1]
        at = r["at"] if isinstance(r, sqlite3.Row) else r[2]
        kind = r["kind"] if isinstance(r, sqlite3.Row) else r[3]
        detail = r["detail"] if isinstance(r, sqlite3.Row) else r[4]
        prev = r["prev_hash"] if isinstance(r, sqlite3.Row) else r[5]
        stored = r["event_hash"] if isinstance(r, sqlite3.Row) else r[6]
        if str(prev or "") != expected_prev:
            return "broken"
        expect = event_mac(
            key,
            prev_hash=str(prev or GENESIS),
            task_id=str(task_id),
            at=str(at),
            kind=str(kind),
            detail=None if detail is None else str(detail),
        )
        if expect != str(stored):
            return "broken"
        expected_prev = str(stored)
        last_hash = str(stored)

    if head is not None and head != last_hash:
        return "broken"
    if head is None:
        # Hashed rows but no seal — treat as broken so wipe-then-reinsert is caught
        # once a seal exists; before first seal after upgrade, set seal on next write.
        return "broken"

    return "ok"
