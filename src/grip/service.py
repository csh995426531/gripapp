from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from grip.accounts import AccountStore
from grip.audit import ExportFormat, build_export, clamp_limit
from grip.cloud_metrics import sample_cloud_metrics as run_cloud_metrics_sample
from grip.metrics import METRICS_HOST_UNREACHABLE, sample_ssh_metrics
from grip.monitor import probe_all, tcp_open
from grip.probes import normalize_probe, wait_probe
from grip.providers import CLOUDS
from grip.remote_agent import (
    build_detached_launch,
    parse_agent_markers,
    reconcile_task_on_host,
    remote_agent_enabled,
)
from grip.resources import Resource, ResourceStore
from grip.ssh_exec import (
    detect_remote_os,
    looks_like_write,
    needs_os_probe,
    remote_file_exists,
    run_ssh,
)
from grip.status import write_snapshot
from grip.sync import sync_all, sync_cloud
from grip.tunnel.route import route_notice
from grip.tasks import TaskStore, normalize_actor
from grip.vault import SecretNotFound, Vault

_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="grip-task")


def _with_route(payload: dict[str, Any], path: str) -> dict[str, Any]:
    payload.update(route_notice(path))
    return payload


def _heartbeat_owns_status(resource: Any) -> bool:
    """Enrolled LAN devices: the three-channel heartbeat is the only status writer."""
    tun = (getattr(resource, "meta", None) or {}).get("tunnel")
    return isinstance(tun, dict) and bool(tun.get("enabled"))


class GripService:
    def __init__(
        self,
        *,
        db_path: Path | None = None,
        vault: Vault | None = None,
    ) -> None:
        self._db_path = db_path
        self.vault = vault or Vault()
        self.resources = ResourceStore(db_path=db_path, vault=self.vault)
        self.tasks = TaskStore(
            db_path=db_path,
            hmac_key_provider=self.vault.audit_hmac_key,
        )
        self.accounts = AccountStore(db_path=db_path, vault=self.vault)
        self._lock = threading.Lock()
        # Serialize L3 samples: native 60s tick + panel 10s poll share one
        # process; overlapping SSH to a slow/dead host stacked 30s waits.
        self._metrics_lock = threading.Lock()

    def refresh_status(self) -> None:
        # Drop ghost 执行中 rows (dead worker / no heartbeat).
        self.tasks.reap_stale_running()
        with self._lock:
            write_snapshot(self.resources, self.tasks)

    def list_resources(self, kind: str = "all") -> dict:
        return {"resources": [r.summary() for r in self.resources.list(kind=kind)]}

    def list_cloud_accounts(self, cloud: str | None = None) -> dict:
        return {
            "accounts": [
                self.accounts.public_for_edit(a) for a in self.accounts.list(cloud)
            ]
        }

    def get_resource(self, name: str) -> dict:
        r = self.resources.get(name)
        if not r:
            return {"error": "resource_not_found", "name": name}
        return {"resource": r.summary()}

    def delete_resource(self, name: str) -> dict:
        """Delete a device. Cloud rows also drop the linked SSH twin and are
        remembered as dismissed until the user confirms restore on sync."""
        name = (name or "").strip()
        r = self.resources.get(name)
        if not r:
            return {"ok": False, "error": "resource_not_found", "name": name}
        deleted = [name]
        twin = None
        if r.kind != "ssh":
            twin = (r.meta or {}).get("ssh_name")
            if isinstance(twin, str):
                twin = twin.strip() or None
            else:
                twin = None
            self.resources.dismiss(name)
        else:
            cloud_ref = (r.meta or {}).get("cloud_ref")
            if isinstance(cloud_ref, str) and cloud_ref.strip():
                # Deleting the hidden SSH twin — also remove / dismiss parent.
                parent = cloud_ref.strip()
                if self.resources.get(parent):
                    self.resources.dismiss(parent)
                    self.resources.delete(parent)
                    deleted.append(parent)
        ok = self.resources.delete(name)
        if twin and self.resources.get(twin):
            self.resources.delete(twin)
            deleted.append(twin)
        self.refresh_status()
        return {"ok": ok, "deleted": deleted, "dismissed": r.kind != "ssh"}

    def reorder_resources(self, names: list[str] | None = None) -> dict:
        """Persist user drag order for the device list (survives restart)."""
        ordered = self.resources.set_list_order(list(names or []))
        self.refresh_status()
        return {"ok": True, "names": ordered}

    def restore_dismissed(self, names: list[str] | None = None) -> dict:
        """Undismiss cloud rows then sync so they reappear in the inventory."""
        wanted = [str(n).strip() for n in (names or []) if str(n).strip()]
        if not wanted:
            return {"ok": False, "error": "names_required", "restored": []}
        restored: list[str] = []
        for n in wanted:
            if n in self.resources.dismissed_names():
                self.resources.undismiss(n)
                restored.append(n)
        # Pull inventory again so undismissed names are upserted (+ SSH link).
        sync_out = self.sync_all()
        return {
            "ok": True,
            "restored": restored,
            "sync": {
                k: sync_out.get(k)
                for k in ("ok", "ssh_linked", "errors", "status", "task_id")
                if k in sync_out
            },
        }

    def list_tasks(self, limit: int = 50, status: str | None = None) -> dict:
        return {
            "tasks": [t.summary_dto() for t in self.tasks.list(limit=limit, status=status)]
        }

    def board(self, limit: int = 100) -> dict:
        tasks = [t.summary_dto() for t in self.tasks.list(limit=limit)]
        by_resource: dict[str, list] = {}
        for t in tasks:
            by_resource.setdefault(t["resource_name"], []).append(t)
        active = [
            t
            for t in tasks
            if t["status"] in {"running", "probing", "queued", "orphaned"}
        ]
        resources = []
        for r in self.resources.list():
            row = r.summary()
            # Surface vault loss without exposing secret material — panel can
            # prompt re-entry instead of failing every run/metrics as opaque errors.
            if r.kind == "ssh" and r.secret_id:
                row["labels"] = dict(row.get("labels") or {})
                row["labels"]["credentials_ok"] = self.vault.has(r.secret_id)
            resources.append(row)
        # 可达/不可达 = SSH L2 探测结果。云实例的 running 是厂商生命周期，不算可达。
        ssh_rows = [r for r in resources if (r.get("kind") or "") == "ssh"]
        return {
            "resources": resources,
            "resource_health": {
                "reachable": sum(
                    1
                    for r in ssh_rows
                    if (r.get("status") or "").lower() == "reachable"
                ),
                "unreachable": sum(
                    1
                    for r in ssh_rows
                    if (r.get("status") or "").lower() == "unreachable"
                ),
                "other": sum(
                    1
                    for r in resources
                    if (r.get("kind") or "") != "ssh"
                    or (r.get("status") or "").lower()
                    not in {"reachable", "unreachable"}
                ),
            },
            "tasks": tasks,
            "by_resource": by_resource,
            "running": active,
            "running_count": len(active),
            "model": "task_session+resource_L1_L2",
            "security": {
                "vault_backend": self.vault.backend,
                "audit_integrity": self.tasks.audit_integrity(),
            },
            "tunnel_setup": self.tunnel_setup_view(),
            "tunnel_job": self.tunnel_job_view(),
        }

    def tunnel_setup_view(self) -> dict:
        from grip.tunnel.setup import rendezvous_plan

        return rendezvous_plan(self.resources)

    def setup_rendezvous(self, name: str, *, reenroll: bool = False) -> dict:
        from grip.tunnel.setup import setup_rendezvous

        return setup_rendezvous(self.resources, self.vault, name, reenroll=reenroll)

    def start_tunnel_job(self, name: str, *, reenroll: bool = False) -> dict:
        from grip.tunnel.setup import start_tunnel_job

        return start_tunnel_job(self.resources, self.vault, name, reenroll=reenroll)

    def tunnel_job_view(self) -> dict:
        from grip.tunnel.setup import tunnel_job_view

        return tunnel_job_view()

    def dismiss_rendezvous_prompt(self, *, dismissed: bool = True) -> dict:
        from grip.tunnel.setup import dismiss_rendezvous_prompt

        return dismiss_rendezvous_prompt(dismissed=dismissed)

    def probe_resources(self, kind: str | None = None) -> dict:
        """L2 reachability: TCP probe LAN SSH + cloud instance IPs. Updates resource status."""
        task = self.tasks.create(
            resource_name="*",
            action="probe_resources",
            command=kind or "all",
        )
        self.tasks.set_running(task.id)
        self.refresh_status()
        try:
            result = probe_all(self.resources, kind=kind)
            finished = self.tasks.finish(
                task.id,
                status="success",
                exit_code=0,
                summary=f"checked={result['checked']} unreachable={result['unreachable']}",
            )
            # Reconcile detached jobs on hosts that just came back reachable.
            for r in self.resources.list(kind="ssh"):
                if (r.status or "").lower() == "reachable":
                    try:
                        self.reconcile_orphaned(name=r.name)
                    except Exception:  # noqa: BLE001
                        pass
            self.refresh_status()
            return {"task_id": finished.id, "status": finished.status, **result}
        except Exception as exc:  # noqa: BLE001
            self.tasks.finish(task.id, status="failed", error=str(exc))
            self.refresh_status()
            return {"task_id": task.id, "status": "failed", "error": str(exc)}

    def maybe_detect_ssh_os(self, name: str) -> str | None:
        """Best-effort OS probe; persists meta.os when detection succeeds.

        No-op when meta.os is already specific (windows/linux/macos). Used after
        add and before metrics so Windows hosts are not stuck on default unix.
        """
        resource = self.resources.get(name)
        if not resource or resource.kind != "ssh":
            return None
        current = resource.meta.get("os")
        if not needs_os_probe(current if isinstance(current, str) else None):
            return (current or "").lower() or None
        try:
            detected = detect_remote_os(resource, self.vault)
        except Exception:  # noqa: BLE001
            return None
        if detected:
            self.resources.patch_meta(name, {"os": detected})
        return detected

    def sample_metrics(self, name: str | None = None) -> dict:
        """L3 host metrics over SSH for one or all ssh resources (cpu load/mem/disk/uptime)."""
        # Non-blocking: if a sample is already in flight (panel + native tick),
        # skip instead of queueing another multi-second SSH attempt.
        if not self._metrics_lock.acquire(blocking=False):
            return {
                "status": "busy",
                "checked": 0,
                "ok": 0,
                "results": [],
                "detail": "metrics_in_flight",
            }
        try:
            return self._sample_metrics_locked(name)
        finally:
            self._metrics_lock.release()

    def _sample_metrics_locked(self, name: str | None = None) -> dict:
        if name:
            resource = self.resources.get(name)
            if not resource:
                return {"error": "resource_not_found", "name": name}
            if resource.kind != "ssh":
                # Linked cloud → prefer SSH host metrics; if that fails, still
                # try vendor metrics so the panel is not blank (Oracle/etc.).
                ssh_name = (resource.meta or {}).get("ssh_name")
                if isinstance(ssh_name, str) and ssh_name.strip():
                    linked = self.resources.get(ssh_name.strip())
                    if linked is not None and linked.kind == "ssh":
                        ssh_out = self._sample_ssh_targets([linked])
                        ssh_ok = int(ssh_out.get("ok") or 0) > 0
                        if ssh_ok:
                            return ssh_out
                        cloud_out = self.sample_cloud_metrics(name=name)
                        return {
                            "status": cloud_out.get("status") or ssh_out.get("status"),
                            "checked": int(ssh_out.get("checked") or 0)
                            + int(cloud_out.get("checked") or 0),
                            "ok": int(ssh_out.get("ok") or 0)
                            + int(cloud_out.get("ok") or 0),
                            "results": list(ssh_out.get("results") or [])
                            + list(cloud_out.get("results") or []),
                            "ssh": ssh_out,
                            "cloud": cloud_out,
                        }
                # No SSH link — best-effort vendor metrics.
                return self.sample_cloud_metrics(name=name)
            return self._sample_ssh_targets([resource])

        # Periodic tick: SSH hosts + vendor metrics for unlinked cloud rows.
        ssh_out = self._sample_ssh_targets(
            [r for r in self.resources.list(kind="ssh")]
        )
        cloud_out = run_cloud_metrics_sample(
            self.resources,
            self.accounts,
            skip_ssh_linked=True,
        )
        self.refresh_status()
        results = list(ssh_out.get("results") or []) + list(
            cloud_out.get("results") or []
        )
        return {
            "status": "success",
            "checked": len(results),
            "ok": int(ssh_out.get("ok") or 0) + int(cloud_out.get("ok") or 0),
            "results": results,
            "ssh": {
                "checked": ssh_out.get("checked"),
                "ok": ssh_out.get("ok"),
            },
            "cloud": {
                "checked": cloud_out.get("checked"),
                "ok": cloud_out.get("ok"),
                "status": cloud_out.get("status"),
            },
        }

    def _sample_ssh_targets(self, targets: list[Any]) -> dict:
        """Sample SSH hosts in parallel (cap ~10) then persist results serially.

        Tuned for ~10–20 devices: each Linux sample sleeps ~1s on the host, so
        a serial loop blew past the 10s panel cadence. SQLite writes stay on
        one thread; only the SSH/TCP work fans out.
        """
        if not targets:
            return {"status": "success", "checked": 0, "ok": 0, "results": []}

        # Persist OS detection from workers without racing the store.
        persist_lock = threading.Lock()
        store = self.resources

        class _LockedPersist:
            def patch_meta(self, name: str, patch: dict[str, Any]) -> None:
                with persist_lock:
                    store.patch_meta(name, patch)

        persist = _LockedPersist()

        def _one(resource: Any) -> tuple[Any, Any]:
            target = resource
            tun = (resource.meta or {}).get("tunnel")
            if isinstance(tun, dict) and tun.get("enabled"):
                from grip.resources import Resource
                from grip.tunnel.ops import ensure_tunnel_ready

                with persist_lock:
                    decision = ensure_tunnel_ready(
                        resource.name,
                        resource.meta,
                        resources=store,
                    )
                if (
                    decision.path != "down"
                    and decision.host
                    and int(decision.port) > 0
                ):
                    meta = dict(resource.meta or {})
                    meta["host"] = decision.host
                    meta["port"] = int(decision.port)
                    target = Resource(
                        name=resource.name,
                        kind=resource.kind,
                        status=resource.status,
                        meta=meta,
                        secret_id=resource.secret_id,
                    )
            sample = sample_ssh_metrics(
                target, self.vault, persist_meta=persist
            )
            return resource, sample

        workers = min(10, max(1, len(targets)))
        by_name: dict[str, tuple[Any, Any]] = {}
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="grip-metrics"
        ) as pool:
            futs = [pool.submit(_one, r) for r in targets]
            for fut in as_completed(futs):
                resource, sample = fut.result()
                by_name[resource.name] = (resource, sample)

        results: list[dict[str, Any]] = []
        ok_n = 0
        # Stable order matching `targets`; all SQLite writes here.
        for resource in targets:
            ent = by_name.get(resource.name)
            if ent is None:
                continue
            resource, sample = ent
            if sample.ok:
                ok_n += 1
                self.resources.apply_metrics(
                    resource.name,
                    sample.metrics,
                    sample.sampled_at,
                    detail=sample.detail,
                )
                if (
                    not _heartbeat_owns_status(resource)
                    and resource.status in {"unknown", "unreachable", "error"}
                ):
                    self.resources.set_status(resource.name, "reachable")
            else:
                # Keep metrics_at / metrics / series at last success — unreachable
                # and other failures must not move the "最近一次成功采样" clock.
                # Enrolled devices keep their dot on the channel heartbeat.
                # A slow metrics SSH must not paint them red or green.
                self.resources.patch_meta(
                    resource.name,
                    {
                        "metrics_attempt_at": sample.sampled_at,
                        "metrics_detail": sample.detail,
                    },
                )
                if (
                    not _heartbeat_owns_status(resource)
                    and sample.detail == METRICS_HOST_UNREACHABLE
                    and resource.status != "unreachable"
                ):
                    self.resources.set_status(resource.name, "unreachable")
            results.append(
                {
                    "name": resource.name,
                    "ok": sample.ok,
                    "metrics": sample.metrics if sample.ok else None,
                    "detail": sample.detail,
                    "sampled_at": sample.sampled_at,
                    "source": "ssh",
                }
            )
        self.refresh_status()
        return {
            "status": "success",
            "checked": len(results),
            "ok": ok_n,
            "results": results,
        }


    def sample_cloud_metrics(
        self,
        cloud: str | None = None,
        name: str | None = None,
        *,
        skip_ssh_linked: bool = False,
    ) -> dict:
        """Best-effort vendor metrics for synced cloud instances (Aliyun CPU today)."""
        if name:
            resource = self.resources.get(name)
            if not resource:
                return {"error": "resource_not_found", "name": name}
        out = run_cloud_metrics_sample(
            self.resources,
            self.accounts,
            cloud=cloud,
            name=name,
            skip_ssh_linked=skip_ssh_linked,
        )
        self.refresh_status()
        return out

    def watch_once(self, *, sync: bool = False, metrics: bool = True) -> dict:
        """One monitoring tick: optional cloud L1 sync + L2 probe + L3 metrics."""
        out: dict[str, Any] = {}
        if sync:
            out["sync"] = self.sync_all()
        out["probe"] = self.probe_resources()
        if metrics:
            out["metrics"] = self.sample_metrics()
        # probe_resources already reconciles reachable hosts; catch leftovers.
        try:
            out["reconcile"] = self.reconcile_orphaned()
        except Exception as exc:  # noqa: BLE001
            out["reconcile"] = {"error": str(exc)}
        self.refresh_status()
        out["board"] = {
            "resource_health": self.board().get("resource_health"),
            "running_count": self.board().get("running_count"),
        }
        return out

    def get_task(self, task_id: str) -> dict:
        t = self.tasks.get(task_id)
        if not t:
            return {"error": "task_not_found", "task_id": task_id}
        dto = t.summary_dto()
        dto["events"] = self.tasks.events(task_id)
        for event in dto["events"]:
            if event.get("kind") != "route" or not event.get("detail"):
                continue
            try:
                notice = json.loads(event["detail"])
            except json.JSONDecodeError:
                continue
            if isinstance(notice, dict):
                dto.update(
                    {
                        "path": notice.get("path"),
                        "route": notice.get("route"),
                        "expect_sec": notice.get("expect_sec"),
                        "note": notice.get("note"),
                    }
                )
            break
        return {"task": dto}

    def export_audit(
        self,
        *,
        name: str | None = None,
        fmt: ExportFormat | str = "jsonl",
        limit: int | None = None,
        action: str | None = None,
    ) -> dict[str, Any]:
        """Export redacted execution history for one device or all."""
        fmt_n = (fmt or "jsonl").lower().strip()
        if fmt_n not in {"json", "jsonl", "csv"}:
            return {"error": "invalid_format", "supported": ["json", "jsonl", "csv"]}
        name_n = (name or "").strip() or None
        lim = clamp_limit(limit)
        tasks = self.tasks.list_audit(name=name_n, limit=lim, action=action)
        body, media_type, filename = build_export(tasks, name=name_n, fmt=fmt_n)  # type: ignore[arg-type]
        return {
            "format": fmt_n,
            "filename": filename,
            "media_type": media_type,
            "count": len(tasks),
            "filter": {"name": name_n, "limit": lim, "action": action},
            "audit_integrity": self.tasks.audit_integrity(),
            "vault_backend": self.vault.backend,
            "body": body,
        }

    def run_command(
        self,
        name: str,
        command: str,
        *,
        timeout_sec: int = 60,
        confirm_write: bool = False,
        background: bool = True,
        await_probe: dict[str, Any] | None = None,
        actor: str | None = None,
        tcp_timeout_sec: float = 2.0,
    ) -> dict:
        """Status = SSH session lifecycle (+ optional probe), not business outcomes.

        Refuses to stay in 执行中 on unreachable hosts: known unreachable status
        or cheap TCP preflight failure → finish failed immediately (主机不可达).
        Mid-run disconnect/timeout → failed; reconnect does not resurrect the task.
        """
        try:
            probe = normalize_probe(await_probe)
        except ValueError as e:
            return {"error": "invalid_probe", "detail": str(e)}

        resource = self.resources.get(name)
        task = self.tasks.create(
            resource_name=name,
            action="run_command",
            command=command,
            probe=probe,
            actor=normalize_actor(actor, default="unknown"),
        )
        if not resource:
            self.tasks.finish(task.id, status="failed", error="resource_not_found")
            self.refresh_status()
            return {"task_id": task.id, "status": "failed", "error": "resource_not_found"}

        if resource.kind != "ssh":
            self.tasks.finish(
                task.id, status="failed", error="run_command_requires_ssh_resource"
            )
            self.refresh_status()
            return {
                "task_id": task.id,
                "status": "failed",
                "error": "run_command_requires_ssh_resource",
            }

        allow_write = os.environ.get("GRIP_ALLOW_WRITE") == "1"
        if looks_like_write(command) and not (confirm_write or allow_write):
            self.tasks.finish(
                task.id, status="denied", error="write_requires_confirm_write=true"
            )
            self.refresh_status()
            return {"task_id": task.id, "status": "denied"}

        # Gate: LAN first (with silence cooldown after fail); else device Node.
        lan_host = str(
            resource.meta.get("lan_host") or resource.meta.get("host") or ""
        ).strip()
        port = int(resource.meta.get("port") or 22)
        tun = resource.meta.get("tunnel")
        tunnel_enabled = isinstance(tun, dict) and bool(tun.get("enabled"))
        exec_host, exec_port = lan_host, port
        path_label = "lan"

        if tunnel_enabled:
            from grip.tunnel.ops import ensure_tunnel_ready

            decision = ensure_tunnel_ready(
                name,
                resource.meta,
                resources=self.resources,
            )
            if decision.path == "down" or not decision.host or decision.port <= 0:
                detail = decision.detail or METRICS_HOST_UNREACHABLE
                if detail in {"tunnel_no_rendezvous", "tunnel_unavailable"}:
                    err = detail
                else:
                    err = METRICS_HOST_UNREACHABLE
                self.tasks.finish(task.id, status="failed", error=err)
                self.refresh_status()
                return _with_route(
                    {
                        "task_id": task.id,
                        "status": "failed",
                        "error": err,
                        "detail": decision.detail,
                    },
                    decision.path,
                )
            exec_host, exec_port = decision.host, int(decision.port)
            path_label = decision.path
        else:
            tcp_ok = bool(lan_host) and tcp_open(
                lan_host, port, timeout=float(tcp_timeout_sec)
            )
            if not tcp_ok:
                if lan_host:
                    self.resources.set_status(name, "unreachable")
                detail = METRICS_HOST_UNREACHABLE
                self.tasks.finish(task.id, status="failed", error=detail)
                self.refresh_status()
                return {
                    "task_id": task.id,
                    "status": "failed",
                    "error": detail,
                }
            self.resources.set_status(name, "reachable")

        self.tasks.set_running(task.id)
        self.tasks.add_event(
            task.id,
            "route",
            json.dumps(route_notice(path_label), ensure_ascii=False),
        )
        self.refresh_status()

        if background:
            _EXECUTOR.submit(
                self._run_ssh_worker,
                task.id,
                name,
                command,
                timeout_sec,
                probe,
                exec_host,
                exec_port,
                path_label,
            )
            return _with_route(
                {
                    "task_id": task.id,
                    "status": "running",
                    "phase": "session",
                    "background": True,
                    "hint": "Poll get_task/board; status tracks session (+ optional probe), not business result",
                },
                path_label,
            )

        out = self._run_ssh_worker(
            task.id, name, command, timeout_sec, probe, exec_host, exec_port, path_label
        )
        if isinstance(out, dict):
            return _with_route(out, path_label)
        return out

    def _heartbeat_loop(self, task_id: str, stop: threading.Event) -> None:
        while not stop.wait(2.0):
            self.tasks.heartbeat(task_id)
            self.refresh_status()

    def _run_ssh_worker(
        self,
        task_id: str,
        name: str,
        command: str,
        timeout_sec: int,
        probe: dict | None,
        exec_host: str | None = None,
        exec_port: int | None = None,
        path_label: str = "lan",
    ) -> dict:
        resource = self.resources.get(name)
        if not resource:
            self.tasks.finish(task_id, status="failed", error="resource_not_found")
            self.refresh_status()
            return {"task_id": task_id, "status": "failed"}

        if exec_host:
            meta = dict(resource.meta)
            meta["host"] = exec_host
            if exec_port is not None:
                meta["port"] = int(exec_port)
            resource = Resource(
                name=resource.name,
                kind=resource.kind,
                status=resource.status,
                meta=meta,
                secret_id=resource.secret_id,
            )

        stop = threading.Event()
        hb = threading.Thread(
            target=self._heartbeat_loop, args=(task_id, stop), daemon=True
        )
        hb.start()
        started_on_host = False
        try:
            # Windows OpenSSH caps CreateProcess argv (~8191). The detached
            # agent script + EncodedCommand exceeds that, so Win runs sync.
            use_agent = remote_agent_enabled() and not (
                (resource.meta.get("os") or "").lower()
                in {"windows", "win", "win32"}
            )
            if use_agent:
                remote_cmd = build_detached_launch(
                    resource,
                    task_id=task_id,
                    command=command,
                    wait_timeout_sec=timeout_sec,
                )
                # Windows: os_wrap uses EncodedCommand; Unix runs under login shell.
                result = run_ssh(
                    resource,
                    self.vault,
                    remote_cmd,
                    timeout_sec=timeout_sec + 30,
                    os_wrap=True,
                    connect_timeout_sec=min(15, max(5, timeout_sec // 2)),
                )
                (
                    started_on_host,
                    _tid,
                    _pid,
                    finished,
                    exit_code,
                    out_tail,
                ) = parse_agent_markers(result.stdout or "")
                # Prefer truncated remote command output; fall back to launcher noise.
                summary = out_tail or (
                    (result.stdout or "")
                    + (("\n" + result.stderr) if result.stderr else "")
                )
                if finished and exit_code is not None:
                    if probe and exit_code == 0:
                        return self._await_probe_after(
                            task_id, name, resource, probe, summary
                        )
                    status = "success" if exit_code == 0 else "failed"
                    finished_t = self.tasks.finish(
                        task_id,
                        status=status,
                        exit_code=exit_code,
                        summary=summary,
                        error=None if exit_code == 0 else "remote_exit_nonzero",
                    )
                    if exit_code == 0:
                        self.resources.set_status(name, "reachable")
                    self.refresh_status()
                    return {
                        "task_id": finished_t.id,
                        "status": finished_t.status,
                    }
                if started_on_host and not finished:
                    # Wait window ended or SSH returned without FINISHED —
                    # job stays detached on host; mark orphaned for reconcile.
                    self.tasks.add_event(
                        task_id, "started_on_host", "detached agent marked start"
                    )
                    # Try a quick reconcile once
                    rec = reconcile_task_on_host(
                        resource, self.vault, task_id, timeout_sec=15
                    )
                    if rec.finished and rec.exit_code is not None:
                        status = "success" if rec.exit_code == 0 else "failed"
                        finished_t = self.tasks.finish(
                            task_id,
                            status=status,
                            exit_code=rec.exit_code,
                            summary=rec.out_tail or summary,
                            error=None if rec.exit_code == 0 else "remote_exit_nonzero",
                        )
                        self.refresh_status()
                        return {
                            "task_id": finished_t.id,
                            "status": finished_t.status,
                        }
                    # Still alive or unknown: mark orphaned (待核对). Do not leave
                    # as running — heartbeat stops when this worker exits and the
                    # stale watchdog would wrongly fail the task.
                    detail = (
                        "detached_still_alive"
                        if rec.alive
                        else "wait_ended_without_finish_marker"
                    )
                    self.tasks.set_orphaned(task_id, detail=detail)
                    self.refresh_status()
                    return {
                        "task_id": task_id,
                        "status": "orphaned",
                        "phase": "reconcile",
                        "detail": detail,
                    }
                # Never saw STARTED — treat as session failure
                finished_t = self.tasks.finish(
                    task_id,
                    status="failed",
                    exit_code=result.exit_code,
                    summary=summary,
                    error=result.stderr or "agent_start_not_confirmed",
                )
                self.refresh_status()
                return {"task_id": finished_t.id, "status": "failed"}

            # Legacy foreground path (GRIP_REMOTE_AGENT=0) — also Windows
            # (agent argv exceeds CreateProcess limit).
            from grip.ssh_exec import scrub_powershell_clixml

            result = run_ssh(resource, self.vault, command, timeout_sec=timeout_sec)
            raw = (result.stdout or "") + (
                ("\n" + result.stderr) if result.stderr else ""
            )
            win = (resource.meta.get("os") or "").lower() in {
                "windows",
                "win",
                "win32",
            }
            summary = scrub_powershell_clixml(raw) if win else raw
            if result.exit_code != 0:
                finished = self.tasks.finish(
                    task_id,
                    status="failed",
                    exit_code=result.exit_code,
                    summary=summary,
                    error=scrub_powershell_clixml(result.stderr or "")
                    or "nonzero_exit",
                )
                # Command failed ≠ host down — SSH session worked.
                if resource.status != "reachable":
                    self.resources.set_status(name, "reachable")
                self.refresh_status()
                return {"task_id": finished.id, "status": finished.status}

            if probe:
                return self._await_probe_after(task_id, name, resource, probe, summary)

            finished = self.tasks.finish(
                task_id,
                status="success",
                exit_code=0,
                summary=summary,
            )
            self.resources.set_status(name, "reachable")
            self.refresh_status()
            return {"task_id": finished.id, "status": finished.status}
        except SecretNotFound:
            self.tasks.finish(task_id, status="failed", error="secret_missing")
            self.refresh_status()
            return {"task_id": task_id, "status": "failed", "error": "secret_missing"}
        except Exception as exc:  # noqa: BLE001
            if started_on_host:
                self.tasks.set_orphaned(task_id, detail=str(exc))
                self.refresh_status()
                return {
                    "task_id": task_id,
                    "status": "orphaned",
                    "error": str(exc),
                    "phase": "reconcile",
                }
            self.tasks.finish(task_id, status="failed", error=str(exc))
            self.refresh_status()
            return {"task_id": task_id, "status": "failed", "error": str(exc)}
        finally:
            stop.set()

    def _await_probe_after(
        self,
        task_id: str,
        name: str,
        resource: Any,
        probe: dict,
        summary: str,
    ) -> dict:
        self.tasks.set_probing(
            task_id, detail=json.dumps(probe, ensure_ascii=False)
        )
        self.refresh_status()

        def check_file(path: str) -> bool:
            return remote_file_exists(resource, self.vault, path)

        ok, msg = wait_probe(
            probe,
            ssh_check_file=check_file if probe["type"] == "file" else None,
        )
        if not ok:
            finished = self.tasks.finish(
                task_id,
                status="failed",
                exit_code=0,
                summary=summary,
                error=msg,
            )
            self.refresh_status()
            return {"task_id": finished.id, "status": "failed", "error": msg}
        summary = (summary + "\n" + msg).strip()
        finished = self.tasks.finish(
            task_id,
            status="success",
            exit_code=0,
            summary=summary,
        )
        self.resources.set_status(name, "reachable")
        self.refresh_status()
        return {"task_id": finished.id, "status": finished.status}

    def reconcile_orphaned(self, name: str | None = None) -> dict:
        """Re-check orphaned tasks on host via remote JSONL / process list."""
        tasks = self.tasks.list_orphaned(name=name, limit=50)
        results: list[dict[str, Any]] = []
        for t in tasks:
            resource = self.resources.get(t.resource_name)
            if not resource or resource.kind != "ssh":
                results.append(
                    {"task_id": t.id, "status": "skipped", "detail": "no_ssh_resource"}
                )
                continue
            try:
                rec = reconcile_task_on_host(
                    resource, self.vault, t.id, timeout_sec=20
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {"task_id": t.id, "status": "orphaned", "detail": str(exc)}
                )
                continue
            if rec.finished and rec.exit_code is not None:
                status = "success" if rec.exit_code == 0 else "failed"
                self.tasks.finish(
                    t.id,
                    status=status,
                    exit_code=rec.exit_code,
                    summary=rec.out_tail or t.summary,
                    error=None if rec.exit_code == 0 else "reconciled_nonzero_exit",
                )
                results.append(
                    {"task_id": t.id, "status": status, "exit_code": rec.exit_code}
                )
            elif rec.alive:
                # Keep orphaned until end marker appears (avoids stale reap).
                self.tasks.add_event(t.id, "reconcile", "still_alive_on_host")
                results.append(
                    {"task_id": t.id, "status": "orphaned", "detail": "alive"}
                )
            elif rec.detail == "UNKNOWN":
                # Host answered: no end marker and no process. Leaving this
                # orphaned makes the list show 执行中 forever.
                self.tasks.finish(
                    t.id,
                    status="failed",
                    error="remote_gone",
                    summary=t.summary,
                )
                results.append(
                    {"task_id": t.id, "status": "failed", "detail": "remote_gone"}
                )
            else:
                results.append(
                    {
                        "task_id": t.id,
                        "status": "orphaned",
                        "detail": rec.detail or "unknown",
                    }
                )
        self.refresh_status()
        return {"checked": len(tasks), "results": results}

    def sync_cloud(self, cloud: str) -> dict:
        cloud = cloud.lower().strip()
        if cloud not in CLOUDS:
            return {"error": "unknown_cloud", "cloud": cloud, "supported": list(CLOUDS)}
        task = self.tasks.create(resource_name=cloud, action="sync_cloud")
        self.tasks.set_running(task.id)
        self.refresh_status()
        try:
            result = sync_cloud(
                self.resources, self.accounts, cloud, vault=self.vault
            )
            linked = int(result.get("ssh_linked") or 0)
            finished = self.tasks.finish(
                task.id,
                status="success",
                exit_code=0,
                summary=(
                    f"synced {result['count']} {cloud} instances"
                    + (f", ssh_linked={linked}" if linked else "")
                ),
            )
            self.refresh_status()
            return {"task_id": finished.id, "status": finished.status, **result}
        except Exception as exc:  # noqa: BLE001
            self.tasks.finish(task.id, status="failed", error=str(exc))
            self.refresh_status()
            return {"task_id": task.id, "status": "failed", "error": str(exc), "cloud": cloud}

    def set_cloud_ssh_defaults(
        self,
        account_id: str,
        *,
        ssh_user: str,
        key_file: str | Path | None = None,
    ) -> dict:
        """Configure instance-login SSH defaults for a cloud account."""
        try:
            acc = self.accounts.set_instance_ssh_defaults(
                account_id,
                ssh_user=ssh_user,
                key_file=Path(key_file) if key_file else None,
            )
        except KeyError:
            return {"error": "account_not_found", "id": account_id}
        except (ValueError, FileNotFoundError, OSError) as exc:
            return {"error": str(exc), "id": account_id}
        self.refresh_status()
        return acc.public()

    def link_cloud_ssh(
        self,
        name: str,
        *,
        ssh_user: str | None = None,
        key_file: str | Path | None = None,
    ) -> dict:
        """Manually open SSH for one cloud instance (uses account defaults or overrides)."""
        resource = self.resources.get(name)
        if not resource or resource.kind == "ssh":
            return {"error": "not_a_cloud_instance", "name": name}
        key_material = None
        user = (ssh_user or "").strip() or None
        if key_file:
            path = Path(key_file).expanduser()
            if not path.is_file():
                return {"error": f"key_file_not_found: {path}", "name": name}
            key_material = path.read_text(encoding="utf-8")
            # Persist as account defaults when we can resolve the account
            cloud = {
                "oracle_instance": "oracle",
                "gcp_instance": "gcp",
                "aliyun_instance": "aliyun",
                "tencent_instance": "tencent",
            }.get(resource.kind)
            label = resource.meta.get("account")
            if cloud and label and user:
                try:
                    self.accounts.set_instance_ssh_defaults(
                        f"{cloud}:{label}",
                        ssh_user=user,
                        key_file=path,
                    )
                except Exception:  # noqa: BLE001
                    pass
        from grip.cloud_ssh import link_ssh_from_cloud

        try:
            ssh = link_ssh_from_cloud(
                self.resources,
                self.vault,
                self.accounts,
                cloud_name=name,
                kind=resource.kind,
                meta=dict(resource.meta),
                ssh_user=user,
                key_material=key_material,
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "name": name}
        if not ssh:
            return {
                "error": "cannot_link",
                "name": name,
                "detail": "need public/private IP and ssh_user + key (account defaults or args)",
            }
        self.refresh_status()
        return {"ok": True, "cloud": name, "ssh": ssh.summary()}

    def sync_all(self) -> dict:
        task = self.tasks.create(resource_name="*", action="sync_all")
        self.tasks.set_running(task.id)
        self.refresh_status()
        try:
            result = sync_all(
                self.resources, self.accounts, vault=self.vault
            )
            ok = result["ok"]
            err_n = len(result["errors"])
            linked = int(result.get("ssh_linked") or 0)
            status = "failed" if ok == 0 and err_n > 0 else "success"
            finished = self.tasks.finish(
                task.id,
                status=status,
                exit_code=0 if status == "success" else 1,
                summary=f"ok={ok} ssh_linked={linked} errors={err_n}",
                error=None
                if not result["errors"]
                else "; ".join(f"{e['cloud']}: {e['error']}" for e in result["errors"]),
            )
            self.refresh_status()
            return {"task_id": finished.id, "status": finished.status, **result}
        except Exception as exc:  # noqa: BLE001
            self.tasks.finish(task.id, status="failed", error=str(exc))
            self.refresh_status()
            return {"task_id": task.id, "status": "failed", "error": str(exc)}

    def sync_oracle(self, profile: str | None = None) -> dict:
        _ = profile
        return self.sync_cloud("oracle")
