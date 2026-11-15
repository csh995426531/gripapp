"""GCP Compute + Monitoring REST via service-account JWT — no google-* SDKs."""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from urllib.parse import quote, urlencode

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from grip.cloud_api._http import http_json_body


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sa_info(secrets: dict[str, Any]) -> dict[str, Any]:
    sa = secrets.get("service_account")
    if isinstance(sa, str):
        sa = json.loads(sa)
    if not isinstance(sa, dict):
        raise RuntimeError("missing service_account JSON")
    return sa


def access_token(sa_info: dict[str, Any], *, scopes: list[str]) -> str:
    """Mint a short-lived OAuth2 access token from a service-account key."""
    now = int(time.time())
    client_email = sa_info["client_email"]
    token_uri = sa_info.get("token_uri") or "https://oauth2.googleapis.com/token"
    private_key = sa_info["private_key"]
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claim = _b64url(
        json.dumps(
            {
                "iss": client_email,
                "scope": " ".join(scopes),
                "aud": token_uri,
                "iat": now,
                "exp": now + 3600,
            }
        ).encode()
    )
    signing_input = f"{header}.{claim}".encode("ascii")
    key = serialization.load_pem_private_key(
        private_key.encode("utf-8"), password=None
    )
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    assertion = f"{header}.{claim}.{_b64url(sig)}"
    body = urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }
    ).encode("utf-8")
    data = http_json_body(
        "POST",
        token_uri,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=body,
    )
    token = data.get("access_token") if isinstance(data, dict) else None
    if not token:
        raise RuntimeError(f"GCP token error: {data}")
    return str(token)


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def aggregated_instances(
    *, sa_info: dict[str, Any], project: str
) -> list[dict[str, Any]]:
    token = access_token(
        sa_info,
        scopes=["https://www.googleapis.com/auth/compute.readonly"],
    )
    url = (
        f"https://compute.googleapis.com/compute/v1/projects/"
        f"{quote(project, safe='')}/aggregated/instances"
    )
    out: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        full = url if not page_token else f"{url}?pageToken={quote(page_token)}"
        data = http_json_body("GET", full, headers=_auth_headers(token))
        items = (data or {}).get("items") or {}
        for zone_key, scoped in items.items():
            for inst in scoped.get("instances") or []:
                if isinstance(inst, dict):
                    inst = dict(inst)
                    inst["_zone_key"] = zone_key
                    out.append(inst)
        page_token = (data or {}).get("nextPageToken")
        if not page_token:
            break
    return out


def query_timeseries(
    *,
    sa_info: dict[str, Any],
    project: str,
    filter_expr: str,
    start_time: str,
    end_time: str,
    aligner: str = "ALIGN_MEAN",
    seconds: int = 60,
) -> list[dict[str, Any]]:
    token = access_token(
        sa_info,
        scopes=["https://www.googleapis.com/auth/monitoring.read"],
    )
    params = {
        "filter": filter_expr,
        "interval.startTime": start_time,
        "interval.endTime": end_time,
        "aggregation.alignmentPeriod": f"{seconds}s",
        "aggregation.perSeriesAligner": aligner,
    }
    qs = urlencode(params)
    url = (
        f"https://monitoring.googleapis.com/v3/projects/"
        f"{quote(project, safe='')}/timeSeries?{qs}"
    )
    data = http_json_body("GET", url, headers=_auth_headers(token))
    return list((data or {}).get("timeSeries") or [])
