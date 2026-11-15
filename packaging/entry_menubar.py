"""PyInstaller entry: native tray / menu-bar UI (no CLI argv)."""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _boot_log(msg: str) -> None:
    try:
        if sys.platform == "darwin":
            base = Path.home() / "Library" / "Logs" / "Grip"
        else:
            base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Grip" / "logs"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "boot.log"
        line = f"[{datetime.now().strftime('%F %T')}] {msg}\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def _patch_codex_ssl_context() -> None:
    """Codex-patched CPython loads ``codex-rejected-cas.pem`` in SSLContext.__new__.

    PyInstaller does not ship that sibling file, so every TLS setup raises
    ``FileNotFoundError: [Errno 2] No such file or directory``. Place the
    bundled PEM when possible; otherwise skip the Codex load on failure.
    """
    try:
        import ssl
    except Exception as exc:
        _boot_log(f"ssl import failed: {exc}")
        return

    pem_name = "codex-rejected-cas.pem"
    bundled: Path | None = None
    meipass = getattr(sys, "_MEIPASS", None)
    search = [
        Path(__file__).resolve().parent / pem_name,
        Path(meipass) / pem_name if meipass else None,
        Path(sys.executable).resolve().parent.parent / "Resources" / pem_name,
        Path(sys.executable).resolve().parent.parent / "Frameworks" / pem_name,
    ]
    for cand in search:
        if cand is not None and cand.is_file():
            bundled = cand
            break

    try:
        ssl_file = getattr(ssl, "__file__", None)
        if ssl_file and bundled is not None:
            target = Path(ssl_file).resolve().parent / pem_name
            if not target.is_file() and target.parent.is_dir():
                try:
                    target.write_bytes(bundled.read_bytes())
                    _boot_log(f"wrote {pem_name} beside ssl.py → {target}")
                except OSError as exc:
                    _boot_log(f"could not write {pem_name}: {exc}")
    except Exception as exc:
        _boot_log(f"ssl pem place skipped: {exc}")

    orig_new = ssl.SSLContext.__new__

    def _safe_new(cls, protocol=None, *args, **kwargs):  # noqa: ANN001
        try:
            return orig_new(cls, protocol, *args, **kwargs)
        except FileNotFoundError:
            if protocol is None:
                protocol = ssl.PROTOCOL_TLS
            return ssl._SSLContext.__new__(cls, protocol)

    ssl.SSLContext.__new__ = _safe_new  # type: ignore[method-assign]
    _boot_log("patched ssl.SSLContext.__new__ for missing codex-rejected-cas.pem")


def _fix_ssl_certs() -> None:
    """Point SSL at the bundled CA file so OCI/HTTPS works in the .app."""
    if not getattr(sys, "frozen", False):
        return
    candidates: list[Path] = []
    try:
        import certifi

        candidates.append(Path(certifi.where()))
    except Exception:
        pass
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "certifi" / "cacert.pem")
    exe = Path(sys.executable).resolve()
    # Grip.app/Contents/MacOS/Grip.bin → Resources / Frameworks
    contents = exe.parent.parent
    # Prefer real Resources files over Frameworks symlinks.
    candidates.append(contents / "Resources" / "certifi" / "cacert.pem")
    candidates.append(contents / "Frameworks" / "certifi" / "cacert.pem")
    for cand in candidates:
        try:
            if cand.is_file():
                real = cand.resolve()
                os.environ["SSL_CERT_FILE"] = str(real)
                os.environ["REQUESTS_CA_BUNDLE"] = str(real)
                _boot_log(f"SSL_CERT_FILE={real}")
                return
        except Exception:
            continue
    _boot_log("WARN: bundled cacert.pem not found")


def _restore_file_vault_env() -> None:
    """If user chose file vault *and* master.key exists, keep using the file backend.

    Do not force GRIP_KEYRING=0 when master.key is missing — that bricks vault.enc
    if the Keychain entry belongs to another Grip home.
    """
    try:
        home = Path(os.environ.get("GRIP_HOME") or (Path.home() / ".grip-app"))
        state = home / "permissions.json"
        if not state.is_file():
            return
        import json

        data = json.loads(state.read_text(encoding="utf-8"))
        if not data.get("file_vault_accepted"):
            return
        if (home / "master.key").is_file():
            os.environ["GRIP_KEYRING"] = "0"
            _boot_log("file_vault_accepted + master.key → GRIP_KEYRING=0")
        else:
            _boot_log(
                "file_vault_accepted but master.key missing; leave keyring enabled"
            )
    except Exception as exc:
        _boot_log(f"file vault env restore skipped: {exc}")


def _dispatch_mode(argv: list[str] | None = None) -> str:
    """Return ``mcp`` when installed app is invoked as Grip … mcp; else menubar.

    Cursor / Claude Desktop should use:
      command: /Applications/Grip.app/Contents/MacOS/Grip
      args: ["mcp"]
    """
    args = list(sys.argv[1:] if argv is None else argv)
    # Ignore macOS LaunchServices noise when opened as a GUI app.
    args = [a for a in args if a and not a.startswith("-psn")]
    if args and args[0] == "mcp":
        return "mcp"
    return "menubar"


if __name__ == "__main__":
    _boot_log(f"entry start frozen={getattr(sys, 'frozen', False)} argv={sys.argv!r}")
    # Packaged app uses its own home so CLI ~/.grip (different Keychain
    # identity) is not mixed. Keychain itself is requested via the in-panel
    # permission gate — do not force GRIP_KEYRING=0 here.
    if getattr(sys, "frozen", False):
        if not os.environ.get("GRIP_HOME"):
            os.environ["GRIP_HOME"] = str(Path.home() / ".grip-app")
        Path(os.environ["GRIP_HOME"]).mkdir(parents=True, exist_ok=True)
        _boot_log(f"frozen env GRIP_HOME={os.environ.get('GRIP_HOME')}")
        _restore_file_vault_env()
        _patch_codex_ssl_context()
        _fix_ssl_certs()
    else:
        # Dev runs can still hit the Codex-patched SSLContext.
        _patch_codex_ssl_context()
    mode = _dispatch_mode()
    _boot_log(f"dispatch mode={mode}")
    try:
        if mode == "mcp":
            from grip.mcp_server import main as mcp_main

            _boot_log("imported mcp_server.main")
            mcp_main()
            _boot_log("mcp_main returned")
        else:
            from grip.native_app import run_native_menubar

            _boot_log("imported run_native_menubar")
            run_native_menubar()
            _boot_log("run_native_menubar returned")
    except SystemExit as exc:
        _boot_log(f"SystemExit: {exc}")
        raise
    except Exception:
        _boot_log("FATAL:\n" + traceback.format_exc())
        raise
