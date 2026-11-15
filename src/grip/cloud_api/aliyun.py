"""Aliyun ECS RPC (DescribeInstances + DescribeInstanceMonitorData) — no SDK."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any
from urllib.parse import quote

from grip.cloud_api._http import http_json_body


def _percent_encode(s: str) -> str:
    return quote(str(s), safe="~").replace("+", "%20").replace("*", "%2A").replace("%7E", "~")


def _sign(secret: str, string_to_sign: str) -> str:
    dig = hmac.new(
        (secret + "&").encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(dig).decode("ascii")


def ecs_rpc(
    *,
    access_key_id: str,
    access_key_secret: str,
    region: str,
    action: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aliyun POP RPC GET to ecs.{region}.aliyuncs.com."""
    common: dict[str, Any] = {
        "Format": "JSON",
        "Version": "2014-05-26",
        "AccessKeyId": access_key_id,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "SignatureVersion": "1.0",
        "SignatureNonce": str(uuid.uuid4()),
        "Action": action,
        "RegionId": region,
    }
    if params:
        common.update(params)
    canonical = "&".join(
        f"{_percent_encode(k)}={_percent_encode(common[k])}" for k in sorted(common)
    )
    string_to_sign = f"GET&%2F&{_percent_encode(canonical)}"
    common["Signature"] = _sign(access_key_secret, string_to_sign)
    query = "&".join(
        f"{_percent_encode(k)}={_percent_encode(common[k])}" for k in sorted(common)
    )
    url = f"https://ecs.{region}.aliyuncs.com/?{query}"
    data = http_json_body("GET", url)
    return data if isinstance(data, dict) else {}


def describe_instances(
    *,
    access_key_id: str,
    access_key_secret: str,
    region: str,
    page_number: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    return ecs_rpc(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        region=region,
        action="DescribeInstances",
        params={"PageNumber": page_number, "PageSize": page_size},
    )


def describe_instance_monitor_data(
    *,
    access_key_id: str,
    access_key_secret: str,
    region: str,
    instance_id: str,
    start_time: str,
    end_time: str,
    period: int = 60,
) -> dict[str, Any]:
    return ecs_rpc(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        region=region,
        action="DescribeInstanceMonitorData",
        params={
            "InstanceId": instance_id,
            "StartTime": start_time,
            "EndTime": end_time,
            "Period": period,
        },
    )


def dumps_for_tests(data: dict[str, Any]) -> str:
    """Helper kept for parity with old SDK raw JSON strings."""
    return json.dumps(data)
