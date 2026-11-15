from __future__ import annotations

import logging
import traceback
from typing import Any

from grip.accounts import AccountStore
from grip.cloud_ssh import link_all_from_sync
from grip.providers import CLOUDS, list_cloud_instances
from grip.redact import strip_forbidden_keys
from grip.resources import ResourceStore
from grip.vault import Vault

log = logging.getLogger("grip.sync")


KIND_MAP = {
    "oracle": "oracle_instance",
    "gcp": "gcp_instance",
    "aliyun": "aliyun_instance",
    "tencent": "tencent_instance",
}


def upsert_instances(
    store: ResourceStore, cloud: str, rows: list[dict[str, Any]]
) -> tuple[int, list[dict[str, Any]]]:
    """Upsert inventory rows. Returns (upserted_count, dismissed_still_present).

    Previously deleted cloud rows stay out of the list until the user confirms
    restore; they are reported so the panel can ask after sync.
    """
    kind = KIND_MAP[cloud]
    dismissed = store.dismissed_names()
    n = 0
    dismissed_present: list[dict[str, Any]] = []
    for row in rows:
        name = row["name"]
        if name in dismissed:
            dismissed_present.append(
                {
                    "name": name,
                    "cloud": cloud,
                    "status": str(row.get("status") or "unknown"),
                }
            )
            continue
        status = str(row.get("status") or "unknown")
        meta = strip_forbidden_keys(
            {k: v for k, v in row.items() if k not in {"name", "status"} and v is not None}
        )
        store.upsert_cloud_instance(name=name, kind=kind, status=status, meta=meta)
        n += 1
    return n, dismissed_present


def sync_cloud(
    store: ResourceStore,
    accounts: AccountStore,
    cloud: str,
    *,
    vault: Vault | None = None,
) -> dict[str, Any]:
    rows = list_cloud_instances(cloud, accounts)
    count, dismissed_present = upsert_instances(store, cloud, rows)
    ssh_linked = 0
    v = vault or store.vault
    try:
        ssh_linked = link_all_from_sync(store, v, accounts, cloud, rows)
    except Exception:  # noqa: BLE001
        ssh_linked = 0
    return {
        "cloud": cloud,
        "count": count,
        "ssh_linked": ssh_linked,
        "dismissed_present": dismissed_present,
    }


def sync_all(
    store: ResourceStore,
    accounts: AccountStore,
    *,
    vault: Vault | None = None,
) -> dict[str, Any]:
    """Sync only clouds that have at least one configured account.

    Missing vendors are skipped (not errors) — e.g. Oracle-only installs
    should not surface ``No GCP account configured``.
    """
    results = []
    errors = []
    skipped: list[str] = []
    dismissed_present: list[dict[str, Any]] = []
    for cloud in CLOUDS:
        if not accounts.list(cloud):
            skipped.append(cloud)
            continue
        try:
            one = sync_cloud(store, accounts, cloud, vault=vault)
            results.append(one)
            dismissed_present.extend(list(one.get("dismissed_present") or []))
        except Exception as exc:  # noqa: BLE001
            detail = str(exc).strip() or repr(exc)
            filename = getattr(exc, "filename", None)
            if filename:
                detail = f"{detail} (path={filename})"
            cause = exc.__cause__ or exc.__context__
            if cause is not None:
                cdetail = str(cause).strip() or repr(cause)
                cfile = getattr(cause, "filename", None)
                detail = f"{detail} | cause={type(cause).__name__}: {cdetail}"
                if cfile:
                    detail = f"{detail} (path={cfile})"
            tb = traceback.format_exc()
            log.error("sync %s failed: %s\n%s", cloud, detail, tb)
            try:
                from pathlib import Path

                log_dir = Path.home() / "Library" / "Logs" / "Grip"
                log_dir.mkdir(parents=True, exist_ok=True)
                (log_dir / "sync-error.log").write_text(
                    f"{cloud}: {detail}\n{tb}\n", encoding="utf-8"
                )
            except Exception:  # noqa: BLE001
                pass
            errors.append(
                {
                    "cloud": cloud,
                    "error": f"{type(exc).__name__}: {detail}",
                    "traceback": tb[-2500:],
                }
            )
    return {
        "results": results,
        "errors": errors,
        "skipped": skipped,
        "ok": sum(r["count"] for r in results),
        "ssh_linked": sum(int(r.get("ssh_linked") or 0) for r in results),
        "dismissed_present": dismissed_present,
    }
