from __future__ import annotations

import re
from typing import Any

FORBIDDEN_KEY_SUBSTR = (
    "password",
    "private_key",
    "api_key",
    "access_key",
    "secret",
    "token",
    "pem",
    "credential",
    "ssh_key",
)

_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_AWS_KEY_RE = re.compile(r"AKIA[0-9A-Z]{16}")
_MAX_SUMMARY = 2048


def redact_text(text: str | None) -> str:
    if not text:
        return ""
    out = _PEM_RE.sub("***REDACTED_PEM***", text)
    out = _AWS_KEY_RE.sub("***REDACTED_AWS_KEY***", out)
    if len(out) > _MAX_SUMMARY:
        out = out[:_MAX_SUMMARY] + "…[truncated]"
    return out


def _key_forbidden(key: str) -> bool:
    low = key.lower()
    return any(s in low for s in FORBIDDEN_KEY_SUBSTR)


def assert_no_secrets(payload: Any, path: str = "$") -> None:
    if isinstance(payload, dict):
        for k, v in payload.items():
            if _key_forbidden(str(k)):
                raise AssertionError(f"forbidden secret key at {path}.{k}")
            if isinstance(v, str) and (
                "BEGIN " in v and "PRIVATE KEY" in v or _AWS_KEY_RE.search(v)
            ):
                raise AssertionError(f"secret material in value at {path}.{k}")
            assert_no_secrets(v, f"{path}.{k}")
    elif isinstance(payload, list):
        for i, item in enumerate(payload):
            assert_no_secrets(item, f"{path}[{i}]")
    elif isinstance(payload, str):
        if "BEGIN " in payload and "PRIVATE KEY" in payload:
            raise AssertionError(f"PEM material at {path}")


def strip_forbidden_keys(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if not _key_forbidden(str(k))}
