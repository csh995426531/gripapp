"""Tencent Cloud TC3 HTTPS (CVM / Lighthouse / Monitor) — no SDK."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from grip.cloud_api._http import http_json_body


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8")
    return hashlib.sha256(s).hexdigest()


def tc3_request(
    *,
    secret_id: str,
    secret_key: str,
    service: str,
    host: str,
    action: str,
    version: str,
    region: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST JSON with TC3-HMAC-SHA256 auth."""
    body_obj = payload or {}
    body = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False)
    timestamp = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_payload = _sha256_hex(body)
    canonical_request = "\n".join(
        [
            "POST",
            "/",
            "",
            canonical_headers,
            signed_headers,
            hashed_payload,
        ]
    )
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join(
        [
            "TC3-HMAC-SHA256",
            str(timestamp),
            credential_scope,
            _sha256_hex(canonical_request),
        ]
    )
    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac_sha256(secret_date, service)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    signature = hmac.new(
        secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    authorization = (
        "TC3-HMAC-SHA256 "
        f"Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": version,
        "X-TC-Region": region,
    }
    data = http_json_body(
        "POST",
        f"https://{host}/",
        headers=headers,
        body=body.encode("utf-8"),
    )
    if not isinstance(data, dict):
        raise RuntimeError(f"tencent {action}: unexpected response")
    resp = data.get("Response") or data
    if isinstance(resp, dict) and resp.get("Error"):
        err = resp["Error"]
        raise RuntimeError(
            f"tencent {action}: {err.get('Code')}: {err.get('Message')}"
        )
    return resp if isinstance(resp, dict) else data


def describe_cvm_instances(
    *, secret_id: str, secret_key: str, region: str
) -> list[dict[str, Any]]:
    resp = tc3_request(
        secret_id=secret_id,
        secret_key=secret_key,
        service="cvm",
        host="cvm.tencentcloudapi.com",
        action="DescribeInstances",
        version="2017-03-12",
        region=region,
        payload={},
    )
    return list(resp.get("InstanceSet") or [])


def describe_lighthouse_instances(
    *, secret_id: str, secret_key: str, region: str
) -> list[dict[str, Any]]:
    resp = tc3_request(
        secret_id=secret_id,
        secret_key=secret_key,
        service="lighthouse",
        host="lighthouse.tencentcloudapi.com",
        action="DescribeInstances",
        version="2020-03-24",
        region=region,
        payload={},
    )
    return list(resp.get("InstanceSet") or [])


def get_monitor_data(
    *,
    secret_id: str,
    secret_key: str,
    region: str,
    namespace: str,
    metric_name: str,
    instance_id: str,
    start_time: str,
    end_time: str,
    period: int = 60,
) -> dict[str, Any]:
    return tc3_request(
        secret_id=secret_id,
        secret_key=secret_key,
        service="monitor",
        host="monitor.tencentcloudapi.com",
        action="GetMonitorData",
        version="2018-07-24",
        region=region,
        payload={
            "Namespace": namespace,
            "MetricName": metric_name,
            "Period": period,
            "StartTime": start_time,
            "EndTime": end_time,
            "Instances": [{"Dimensions": [{"Name": "InstanceId", "Value": instance_id}]}],
        },
    )
