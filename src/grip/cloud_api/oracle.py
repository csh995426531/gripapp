"""Oracle Cloud Infrastructure REST (compute + monitoring) — no oci SDK."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from email.utils import formatdate
from typing import Any
from urllib.parse import quote

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from grip.cloud_api._http import http_json


def _load_private_key(pem: str):
    from pathlib import Path

    text = (pem or "").strip()
    if not text:
        raise ValueError(
            "Oracle API 私钥为空 — 请打开云账号重新选择 PEM 私钥后再同步"
        )
    # Older/broken saves may store a filesystem path instead of PEM body.
    if not text.startswith("-----"):
        path = Path(text).expanduser()
        if not path.is_file():
            raise FileNotFoundError(
                f"Oracle API 私钥文件不存在: {path} — 请在云账号里重新选择私钥"
            )
        text = path.read_text(encoding="utf-8").strip()
        if not text.startswith("-----"):
            raise ValueError(f"不是有效的 PEM 私钥: {path}")
    return serialization.load_pem_private_key(text.encode("utf-8"), password=None)


def _oci_host(service: str, region: str) -> str:
    if service == "telemetry":
        return f"telemetry.{region}.oraclecloud.com"
    return f"iaas.{region}.oraclecloud.com"


def _auth_kwargs(secrets: dict[str, Any]) -> dict[str, str]:
    key = secrets.get("private_key") or ""
    return {
        "user_ocid": str(secrets["user_ocid"]),
        "tenancy_ocid": str(secrets["tenancy_ocid"]),
        "fingerprint": str(secrets["fingerprint"]),
        "private_key_pem": str(key),
    }


def _signed_request(
    *,
    method: str,
    service: str,
    region: str,
    path: str,
    user_ocid: str,
    tenancy_ocid: str,
    fingerprint: str,
    private_key_pem: str,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, str]]:
    host = _oci_host(service, region)
    items_q = []
    for k in sorted(query or {}):
        v = (query or {})[k]
        if v is None or v == "":
            continue
        items_q.append(f"{quote(str(k), safe='')}={quote(str(v), safe='')}")
    qs = "&".join(items_q)
    target = f"{path}?{qs}" if qs else path
    url = f"https://{host}{target}"

    method_u = method.upper()
    date = formatdate(usegmt=True)
    headers_lower: dict[str, str] = {
        "host": host,
        "date": date,
        "accept": "application/json",
    }
    body_bytes: bytes | None = None
    if body is not None:
        body_bytes = json.dumps(body).encode("utf-8")
        headers_lower["content-type"] = "application/json"
        headers_lower["content-length"] = str(len(body_bytes))
        headers_lower["x-content-sha256"] = base64.b64encode(
            hashlib.sha256(body_bytes).digest()
        ).decode("ascii")

    if method_u in ("POST", "PUT", "PATCH"):
        signing_headers = [
            "(request-target)",
            "host",
            "date",
            "x-content-sha256",
            "content-type",
            "content-length",
        ]
    else:
        signing_headers = ["(request-target)", "host", "date"]

    request_target = f"{method_u.lower()} {target}"
    lines = []
    for name in signing_headers:
        if name == "(request-target)":
            lines.append(f"(request-target): {request_target}")
        else:
            lines.append(f"{name}: {headers_lower[name]}")
    signing_string = "\n".join(lines)

    key = _load_private_key(private_key_pem)
    signature = key.sign(
        signing_string.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    key_id = f"{tenancy_ocid}/{user_ocid}/{fingerprint}"
    authorization = (
        'Signature version="1",'
        f'keyId="{key_id}",'
        'algorithm="rsa-sha256",'
        f'headers="{" ".join(signing_headers)}",'
        f'signature="{base64.b64encode(signature).decode("ascii")}"'
    )

    out_headers = {
        "Host": host,
        "Date": date,
        "Accept": "application/json",
        "Authorization": authorization,
    }
    if body is not None:
        out_headers["Content-Type"] = headers_lower["content-type"]
        out_headers["Content-Length"] = headers_lower["content-length"]
        out_headers["x-content-sha256"] = headers_lower["x-content-sha256"]

    return http_json(
        method_u,
        url,
        headers=out_headers,
        body=body_bytes,
    )


def list_instances(
    *,
    secrets: dict[str, Any],
    region: str,
    compartment_id: str,
) -> list[dict[str, Any]]:
    auth = _auth_kwargs(secrets)
    out: list[dict[str, Any]] = []
    page: str | None = None
    while True:
        query: dict[str, Any] = {"compartmentId": compartment_id}
        if page:
            query["page"] = page
        data, hdrs = _signed_request(
            method="GET",
            service="iaas",
            region=region,
            path="/20160918/instances",
            query=query,
            **auth,
        )
        if isinstance(data, list):
            out.extend(x for x in data if isinstance(x, dict))
        elif isinstance(data, dict):
            out.append(data)
        page = hdrs.get("opc-next-page")
        if not page:
            break
    return out


def list_vnic_attachments(
    *,
    secrets: dict[str, Any],
    region: str,
    compartment_id: str,
    instance_id: str,
) -> list[dict[str, Any]]:
    auth = _auth_kwargs(secrets)
    data, _ = _signed_request(
        method="GET",
        service="iaas",
        region=region,
        path="/20160918/vnicAttachments",
        query={"compartmentId": compartment_id, "instanceId": instance_id},
        **auth,
    )
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def get_vnic(
    *,
    secrets: dict[str, Any],
    region: str,
    vnic_id: str,
) -> dict[str, Any]:
    auth = _auth_kwargs(secrets)
    data, _ = _signed_request(
        method="GET",
        service="iaas",
        region=region,
        path=f"/20160918/vnics/{quote(vnic_id, safe='')}",
        **auth,
    )
    return data if isinstance(data, dict) else {}


def summarize_metrics_data(
    *,
    secrets: dict[str, Any],
    region: str,
    compartment_id: str,
    namespace: str,
    query: str,
    start_time: datetime,
    end_time: datetime,
) -> list[dict[str, Any]]:
    auth = _auth_kwargs(secrets)

    def _iso(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    body = {
        "namespace": namespace,
        "query": query,
        "startTime": _iso(start_time),
        "endTime": _iso(end_time),
    }
    data, _ = _signed_request(
        method="POST",
        service="telemetry",
        region=region,
        path="/20180401/metrics/actions/summarizeMetricsData",
        query={"compartmentId": compartment_id},
        body=body,
        **auth,
    )
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []
