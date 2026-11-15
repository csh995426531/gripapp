from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from grip.cloud_api import gcp as gcp_api
from grip.providers._cli import slug

if TYPE_CHECKING:
    from grip.accounts import AccountStore, CloudAccount


def list_for_account(account: "CloudAccount", secrets: dict[str, Any]) -> list[dict[str, Any]]:
    sa_info = secrets.get("service_account")
    if isinstance(sa_info, str):
        sa_info = json.loads(sa_info)
    if not sa_info:
        raise RuntimeError(f"{account.id}: missing service_account JSON in vault")
    project = account.meta.get("project") or sa_info.get("project_id")
    if not project:
        raise RuntimeError(f"{account.id}: missing project")
    out: list[dict[str, Any]] = []
    for i in gcp_api.aggregated_instances(sa_info=sa_info, project=str(project)):
        zone_key = i.get("_zone_key") or ""
        z = str(zone_key).split("/")[-1] if zone_key else (i.get("zone") or "").rsplit("/", 1)[-1]
        region = re.sub(r"-[a-z]$", "", z)
        status = (i.get("status") or "").upper()
        state = {"RUNNING": "running", "TERMINATED": "stopped"}.get(
            status, status.lower() or "unknown"
        )
        prv = None
        pub = None
        nics = i.get("networkInterfaces") or []
        if nics:
            nic = nics[0]
            prv = nic.get("networkIP")
            access = nic.get("accessConfigs") or []
            if access:
                pub = access[0].get("natIP")
        mtype = (i.get("machineType") or "").rsplit("/", 1)[-1]
        name = i.get("name") or str(i.get("id"))
        out.append(
            {
                "cloud": "gcp",
                "name": f"gcp:{account.label}:{name}",
                "id": str(i.get("id") or name),
                "status": state,
                "region": region,
                "zone": z,
                "shape": mtype,
                "private_ip": prv,
                "public_ip": pub,
                "project": project,
                "account": account.label,
                "ssh_alias": "gcp-" + slug(str(name)),
            }
        )
    return out


def list_gcp_instances(accounts: "AccountStore") -> list[dict[str, Any]]:
    rows = accounts.list("gcp")
    if not rows:
        raise RuntimeError(
            "No GCP account configured. Run: grip cloud-add gcp --label ... --sa-json <file>"
        )
    out: list[dict[str, Any]] = []
    for acc in rows:
        out.extend(list_for_account(acc, accounts.secret_dict(acc)))
    return out
