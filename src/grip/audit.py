"""Audit trail export — redacted execution history (no vault / secrets)."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Literal

from grip.redact import assert_no_secrets, redact_text
from grip.tasks import Task, normalize_actor

ExportFormat = Literal["json", "jsonl", "csv"]

_DEFAULT_LIMIT = 2000
_MAX_LIMIT = 10000
_EXCERPT = 512

__all__ = [
    "ExportFormat",
    "build_export",
    "clamp_limit",
    "entry_from_task",
    "normalize_actor",
    "resolve_mcp_actor",
]


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return _DEFAULT_LIMIT
    return max(1, min(int(limit), _MAX_LIMIT))


def resolve_mcp_actor(
    actor: str | None = None,
    *,
    client_name: str | None = None,
) -> str:
    """MCP default actor=mcp; optional caller label or protocol clientInfo.name."""
    explicit = (actor or "").strip()
    if explicit:
        return normalize_actor(explicit, default="mcp")
    name = (client_name or "").strip()
    if name:
        return normalize_actor(f"mcp:{name}", default="mcp")
    return "mcp"


def entry_from_task(task: Task) -> dict[str, Any]:
    """One audit row — redacted; never vault."""
    detail_parts: list[str] = []
    if task.summary:
        detail_parts.append(task.summary)
    if task.error:
        detail_parts.append(f"error: {task.error}")
    detail = redact_text("\n".join(detail_parts) if detail_parts else None)
    if len(detail) > _EXCERPT:
        detail = detail[:_EXCERPT] + "…[truncated]"
    command = redact_text(task.command) if task.command else None
    if command and len(command) > _EXCERPT:
        command = command[:_EXCERPT] + "…[truncated]"
    row = {
        "timestamp": task.created_at,
        "finished_at": task.finished_at,
        "task_id": task.id,
        "resource": task.resource_name,
        "actor": task.actor or "unknown",
        "action": task.action,
        "command": command,
        "status": task.status,
        "exit_code": task.exit_code,
        "detail": detail or None,
    }
    assert_no_secrets(row)
    return row


def build_export(
    tasks: list[Task],
    *,
    name: str | None = None,
    fmt: ExportFormat = "jsonl",
) -> tuple[str, str, str]:
    """Return (body, media_type, filename_stem_suffix)."""
    entries = [entry_from_task(t) for t in tasks]
    exported_at = datetime.now(timezone.utc).isoformat()
    scope = name or "all"
    safe_scope = "".join(c if c.isalnum() or c in "-_" else "_" for c in scope)[:64] or "all"
    stamp = exported_at.replace(":", "").replace("+", "Z")[:15]

    if fmt == "json":
        payload = {
            "exported_at": exported_at,
            "filter": {"name": name},
            "count": len(entries),
            "entries": entries,
        }
        assert_no_secrets(payload)
        body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        return body, "application/json; charset=utf-8", f"grip-audit-{safe_scope}-{stamp}.json"

    if fmt == "csv":
        buf = io.StringIO()
        fields = [
            "timestamp",
            "finished_at",
            "task_id",
            "resource",
            "actor",
            "action",
            "command",
            "status",
            "exit_code",
            "detail",
        ]
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for e in entries:
            writer.writerow({k: e.get(k) if e.get(k) is not None else "" for k in fields})
        body = buf.getvalue()
        return body, "text/csv; charset=utf-8", f"grip-audit-{safe_scope}-{stamp}.csv"

    # jsonl default
    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    body = ("\n".join(lines) + ("\n" if lines else ""))
    return body, "application/x-ndjson; charset=utf-8", f"grip-audit-{safe_scope}-{stamp}.jsonl"
