from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from grip.paths import status_path
from grip.redact import assert_no_secrets
from grip.resources import ResourceStore
from grip.tasks import TaskStore


def build_snapshot(
    resources: ResourceStore,
    tasks: TaskStore,
) -> dict:
    res = [r.summary() for r in resources.list()]
    # More tasks for multi-host board
    task_dtos = [t.summary_dto() for t in tasks.list(limit=100)]
    running = sum(1 for t in task_dtos if t["status"] == "running")
    failed = sum(1 for t in task_dtos if t["status"] == "failed")
    by_cloud = Counter()
    for r in res:
        kind = r.get("kind") or ""
        if kind.endswith("_instance"):
            by_cloud[kind.replace("_instance", "")] += 1
        elif kind == "ssh":
            by_cloud["ssh"] += 1
        else:
            by_cloud[kind or "other"] += 1
    by_resource: dict[str, list] = defaultdict(list)
    for t in task_dtos:
        by_resource[t["resource_name"]].append(t)
    title_parts = []
    if running:
        title_parts.append(f"{running}…")
    if failed:
        title_parts.append(f"{failed}✗")
    if by_cloud:
        title_parts.append(
            " ".join(f"{k[0].upper()}{v}" for k, v in sorted(by_cloud.items()))
        )
    else:
        title_parts.append("0▣")
    snap = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "resources": res,
        "tasks": task_dtos,
        "by_resource": dict(by_resource),
        "clouds": dict(by_cloud),
        "running_count": running,
        "title": " ".join(title_parts),
    }
    assert_no_secrets(snap)
    return snap


def write_snapshot(
    resources: ResourceStore,
    tasks: TaskStore,
    path: Path | None = None,
) -> Path:
    dest = path or status_path()
    snap = build_snapshot(resources, tasks)
    dest.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest
