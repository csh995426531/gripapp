from __future__ import annotations

"""First-run permission gate (Keychain + file access).

Packaged / menubar apps check these before unlocking the main panel. Clicking
「去授权」triggers the OS prompt on the UI main thread; we re-check afterwards.
Unsigned builds often cannot write Keychain — users can accept the file vault.
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from grip.paths import ensure_home

_PROBE_ACCOUNT = "_grip_perm_probe"
_STATE_NAME = "permissions.json"


def _state_path() -> Path:
    return ensure_home() / _STATE_NAME


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_state(patch: dict[str, Any]) -> dict[str, Any]:
    cur = _load_state()
    cur.update(patch)
    cur["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = _state_path()
    path.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return cur


def _file_vault_accepted() -> bool:
    return bool(_load_state().get("file_vault_accepted"))


def check_keychain(*, interactive: bool = False) -> dict[str, Any]:
    """Probe OS keyring with a short-lived canary secret.

    When ``interactive`` is True, run on the AppKit main thread so macOS can
    show the Keychain access dialog (background threads show nothing).
    """
    from grip import key_store

    if _file_vault_accepted():
        return {
            "id": "keychain",
            "title": "钥匙串" if sys.platform == "darwin" else "凭据保管",
            "blurb": "已改用本机加密文件保管主密钥（未签名包常用此方式）。",
            "ok": True,
            "detail": "文件保险库",
            "required": True,
            "mode": "file",
        }

    detail = ""
    ok = False
    mode = "keyring"

    def _probe(*, direct: bool) -> tuple[bool, str]:
        token = f"ok-{int(time.time())}"
        wrote = key_store.set_secret(
            _PROBE_ACCOUNT, token.encode("utf-8"), direct=direct
        )
        if not wrote:
            return False, "无法写入钥匙串 / 凭据管理器（可能被拒绝或超时）"
        got = key_store.get_secret(_PROBE_ACCOUNT, direct=direct)
        good = got == token.encode("utf-8")
        try:
            key_store.delete_secret(_PROBE_ACCOUNT)
        except Exception:
            pass
        return good, ("已授权" if good else "写入成功但读回失败")

    try:
        if interactive:
            from grip.native_main import has_main_runner, run_on_main

            if has_main_runner():
                ok, detail = run_on_main(lambda: _probe(direct=True))
            else:
                # Dev / no native UI — try direct on this thread with patience.
                ok, detail = _probe(direct=True)
        else:
            ok, detail = _probe(direct=False)
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)[:200]
        ok = False

    return {
        "id": "keychain",
        "title": "钥匙串" if sys.platform == "darwin" else "凭据保管",
        "blurb": (
            "用于安全存放 SSH / 云密钥的主密钥。"
            if sys.platform == "darwin"
            else "用于安全存放 SSH / 云密钥的主密钥（Windows 凭据管理器）。"
        ),
        "ok": ok,
        "detail": detail,
        "required": True,
        "mode": mode,
        "can_use_file_vault": not ok,
    }


def check_files() -> dict[str, Any]:
    """User-home file access (SSH keys under ~/.ssh)."""
    ssh = Path.home() / ".ssh"
    detail = ""
    ok = False
    try:
        if ssh.is_dir():
            _ = list(ssh.iterdir())
            ok = True
            detail = f"可访问 {ssh}"
        else:
            home = Path.home()
            _ = list(home.iterdir())
            ok = True
            detail = "主目录可读（尚未有 ~/.ssh）"
        remembered = bool(_load_state().get("files_granted"))
        if remembered:
            ok = True
            if not detail:
                detail = "已完成文件授权引导"
    except PermissionError:
        ok = False
        detail = "系统拒绝访问主目录 / .ssh，需要授权文件访问"
    except Exception as exc:  # noqa: BLE001
        ok = False
        detail = str(exc)[:200]
    return {
        "id": "files",
        "title": "文件访问",
        "blurb": "选取并读取本机 SSH 私钥（默认从 ~/.ssh）。",
        "ok": ok,
        "detail": detail,
        "required": True,
    }


def status() -> dict[str, Any]:
    items = [check_keychain(), check_files()]
    ready = all(bool(i.get("ok")) for i in items if i.get("required"))
    if ready:
        _save_state({"ready": True, "keychain": True, "files": True})
    return {
        "platform": sys.platform,
        "ready": ready,
        "items": items,
        "gate": not ready,
        "file_vault_accepted": _file_vault_accepted(),
    }


def accept_file_vault() -> dict[str, Any]:
    """User opts into on-disk encrypted vault when Keychain is unavailable."""
    import os

    # Prefer file backend for this app home going forward.
    os.environ["GRIP_KEYRING"] = "0"
    _save_state(
        {
            "file_vault_accepted": True,
            "keychain": True,
            "ready": False,  # status() will recompute with files
        }
    )
    return {"ok": True, "item": check_keychain(), "status": status()}


def request(perm_id: str) -> dict[str, Any]:
    """Trigger the interactive OS prompt for one permission, then re-check."""
    pid = (perm_id or "").strip().lower()
    if pid in {"keychain", "keyring"}:
        item = check_keychain(interactive=True)
        if item["ok"]:
            _save_state({"keychain": True})
        return {"ok": item["ok"], "item": item, "status": status()}

    if pid in {"file_vault", "file-vault", "skip_keychain"}:
        return accept_file_vault()

    if pid == "files":
        from grip.native_pick import choose_open_path, default_ssh_key_dir

        path, st = choose_open_path(
            prompt="选择一个文件以授权 Grip 访问磁盘（推荐 ~/.ssh 下的私钥）",
            start_dir=default_ssh_key_dir(),
        )
        if st == "ok" and path:
            _save_state({"files_granted": True, "files": True})
        elif st == "cancelled":
            _save_state({"files_dialog_seen": True})
        item = check_files()
        if item["ok"]:
            _save_state({"files": True})
        return {
            "ok": item["ok"],
            "item": item,
            "picked": path,
            "picker_status": st,
            "status": status(),
        }

    return {"ok": False, "error": "unknown_permission", "status": status()}
