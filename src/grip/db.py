from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from grip.paths import db_path, ensure_home


SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
  name TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'unknown',
  meta_json TEXT NOT NULL DEFAULT '{}',
  secret_id TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  resource_name TEXT NOT NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  exit_code INTEGER,
  summary TEXT,
  error TEXT,
  command TEXT,
  last_heartbeat_at TEXT,
  phase TEXT,
  probe_json TEXT,
  actor TEXT NOT NULL DEFAULT 'unknown'
);

CREATE TABLE IF NOT EXISTS task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT,
  prev_hash TEXT,
  event_hash TEXT
);

CREATE TABLE IF NOT EXISTS grip_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    alters = []
    if "last_heartbeat_at" not in cols:
        alters.append("ALTER TABLE tasks ADD COLUMN last_heartbeat_at TEXT")
    if "phase" not in cols:
        alters.append("ALTER TABLE tasks ADD COLUMN phase TEXT")
    if "probe_json" not in cols:
        alters.append("ALTER TABLE tasks ADD COLUMN probe_json TEXT")
    if "actor" not in cols:
        # Old rows get unknown; new inserts always set actor explicitly.
        alters.append(
            "ALTER TABLE tasks ADD COLUMN actor TEXT NOT NULL DEFAULT 'unknown'"
        )
    for sql in alters:
        conn.execute(sql)

    ev_cols = {r[1] for r in conn.execute("PRAGMA table_info(task_events)").fetchall()}
    if "prev_hash" not in ev_cols:
        conn.execute("ALTER TABLE task_events ADD COLUMN prev_hash TEXT")
    if "event_hash" not in ev_cols:
        conn.execute("ALTER TABLE task_events ADD COLUMN event_hash TEXT")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS grip_meta (
             key TEXT PRIMARY KEY,
             value TEXT NOT NULL
           )"""
    )


def connect(path: Path | None = None) -> sqlite3.Connection:
    ensure_home()
    p = path or db_path()
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def meta_dumps(meta: dict) -> str:
    return json.dumps(meta, ensure_ascii=False, sort_keys=True)


def meta_loads(raw: str | None) -> dict:
    if not raw:
        return {}
    return json.loads(raw)
