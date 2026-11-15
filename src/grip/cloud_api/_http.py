from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _resolve_cafile() -> str | None:
    """Return a real, readable CA bundle path (never a dangling symlink)."""
    candidates: list[str] = []
    env = (os.environ.get("SSL_CERT_FILE") or "").strip()
    if env:
        candidates.append(env)
    try:
        import certifi

        cand = certifi.where()
        if cand:
            candidates.append(cand)
    except Exception:  # noqa: BLE001
        pass
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(str(Path(meipass) / "certifi" / "cacert.pem"))
    try:
        exe = Path(sys.executable).resolve()
        contents = exe.parent.parent
        # Prefer Resources (real files) over Frameworks (often a symlink).
        candidates.append(str(contents / "Resources" / "certifi" / "cacert.pem"))
        candidates.append(str(contents / "Frameworks" / "certifi" / "cacert.pem"))
    except Exception:  # noqa: BLE001
        pass

    seen: set[str] = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        try:
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
            if os.path.isfile(real) and os.path.getsize(real) > 0:
                # Prove Python can open it (OpenSSL is pickier about symlinks).
                with open(real, "rb") as fh:
                    if not fh.read(1):
                        continue
                return real
        except OSError:
            continue
    return None


def _ssl_context() -> ssl.SSLContext:
    """Build a TLS client context that works inside a PyInstaller .app.

    Bare ``ssl.create_default_context()`` fails in some frozen macOS builds
    (OpenSSL default paths / Frameworks→Resources certifi symlink). Always
    load an explicit CA bundle via realpath when available.
    """
    cafile = _resolve_cafile()
    if cafile:
        os.environ["SSL_CERT_FILE"] = cafile
        os.environ["REQUESTS_CA_BUNDLE"] = cafile
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_verify_locations(cafile=cafile)
        return ctx

    # Last resort — may still fail in broken frozen SSL layouts.
    try:
        return ssl.create_default_context()
    except FileNotFoundError as exc:
        raise RuntimeError(
            "无法初始化 TLS：打包应用缺少 CA 证书包（certifi/cacert.pem）"
        ) from exc


def http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 60.0,
) -> Any:
    req = urllib.request.Request(url, data=body, method=method.upper())
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(
            req, timeout=timeout, context=_ssl_context()
        ) as resp:
            raw = resp.read()
            header_map = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"HTTP {exc.code} {url}: {err_body}") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        detail = str(reason) if reason is not None else str(exc)
        raise RuntimeError(f"网络请求失败 {url}: {detail}") from exc
    except FileNotFoundError as exc:
        missing = getattr(exc, "filename", None)
        hint = f" path={missing!r}" if missing else ""
        raise RuntimeError(f"网络请求缺文件 {url}: {exc}{hint}") from exc
    except OSError as exc:
        missing = getattr(exc, "filename", None)
        hint = f" path={missing!r}" if missing else ""
        raise RuntimeError(f"网络请求失败 {url}: {exc}{hint}") from exc
    if not raw:
        return {}, header_map
    data = json.loads(raw.decode("utf-8"))
    return data, header_map


def http_json_body(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 60.0,
) -> Any:
    data, _ = http_json(
        method, url, headers=headers, body=body, timeout=timeout
    )
    return data
