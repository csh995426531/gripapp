from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from grip import __version__
from grip.paths import ensure_home
from grip.service import GripService
from grip.status import write_snapshot


def _add_cloud(svc: GripService, args: argparse.Namespace) -> dict:
    cloud = args.cloud.lower()
    label = args.label
    if cloud == "aliyun":
        meta = {"regions": args.region, "ak_id": args.access_key_id or ""}
        secret = {
            "access_key_id": args.access_key_id,
            "access_key_secret": args.access_key_secret,
        }
    elif cloud == "tencent":
        meta = {
            "regions": args.region,
            "product": args.product,
            "tc_id": args.secret_id or "",
        }
        secret = {"secret_id": args.secret_id, "secret_key": args.secret_key}
    elif cloud == "gcp":
        sa_path = Path(args.sa_json).expanduser()
        sa = json.loads(sa_path.read_text(encoding="utf-8"))
        meta = {
            "project": args.project or sa.get("project_id"),
            "client_email": sa.get("client_email") or "",
            "project_id": sa.get("project_id") or "",
        }
        secret = {"service_account": sa}
    elif cloud == "oracle":
        key = Path(args.key_file).expanduser().read_text(encoding="utf-8")
        meta = {
            "compartment_id": args.compartment_id,
            "regions": args.region,
            "user_ocid": args.user_ocid or "",
            "tenancy_ocid": args.tenancy_ocid or "",
            "fingerprint": args.fingerprint or "",
        }
        secret = {
            "user_ocid": args.user_ocid,
            "tenancy_ocid": args.tenancy_ocid,
            "fingerprint": args.fingerprint,
            "private_key": key,
            "compartment_id": args.compartment_id,
            "region": (args.region or ["ap-tokyo-1"])[0],
        }
    else:
        raise SystemExit(f"unsupported cloud {cloud}")
    acc = svc.accounts.upsert(
        cloud=cloud, label=label, meta=meta, secret_payload=secret
    )
    ssh_user = getattr(args, "ssh_user", None)
    ssh_key = getattr(args, "ssh_key_file", None)
    if ssh_user and ssh_key:
        acc = svc.accounts.set_instance_ssh_defaults(
            acc.id, ssh_user=ssh_user, key_file=Path(ssh_key)
        )
    return acc.public()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grip", description="Grip secure MCP resource gateway (bundled cloud SDKs)"
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("mcp", help="Run MCP server on stdio")

    p_cloud = sub.add_parser("cloud-add", help="Add cloud account (secrets → local vault)")
    p_cloud.add_argument("cloud", choices=["oracle", "gcp", "aliyun", "tencent"])
    p_cloud.add_argument("--label", required=True)
    p_cloud.add_argument("--region", action="append", default=[])
    # aliyun
    p_cloud.add_argument("--access-key-id")
    p_cloud.add_argument("--access-key-secret")
    # tencent
    p_cloud.add_argument("--secret-id")
    p_cloud.add_argument("--secret-key")
    p_cloud.add_argument("--product", default="lighthouse")
    # gcp
    p_cloud.add_argument("--sa-json", help="GCP service account JSON file")
    p_cloud.add_argument("--project")
    # oracle
    p_cloud.add_argument("--user-ocid")
    p_cloud.add_argument("--tenancy-ocid")
    p_cloud.add_argument("--fingerprint")
    p_cloud.add_argument("--key-file", help="OCI API private key PEM")
    p_cloud.add_argument("--compartment-id")
    p_cloud.add_argument(
        "--ssh-user",
        help="Instance SSH login user (opc/ubuntu); enables auto SSH on sync",
    )
    p_cloud.add_argument(
        "--ssh-key-file",
        help="Instance SSH private key PEM (not the API signing key)",
    )

    sub.add_parser("cloud-list", help="List cloud accounts (no secrets)")

    p_add = sub.add_parser(
        "resource-add-ssh",
        help="Register an SSH host (key OR password)",
    )
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--host", required=True)
    p_add.add_argument("--user", required=True)
    p_add.add_argument("--key-file", default="", help="Private key path (xor password)")
    p_add.add_argument(
        "--password",
        default="",
        help="SSH password (xor key-file); prefer --password-prompt",
    )
    p_add.add_argument(
        "--password-prompt",
        action="store_true",
        help="Read password from terminal (not shown)",
    )
    p_add.add_argument("--port", type=int, default=22)
    p_add.add_argument(
        "--os",
        default="unix",
        choices=["unix", "linux", "macos", "windows"],
        help="Remote OS; windows uses PowerShell over OpenSSH",
    )

    p_upd = sub.add_parser(
        "resource-update-ssh",
        help="Update SSH host / user / port / os / credentials (name immutable)",
    )
    p_upd.add_argument("--name", required=True)
    p_upd.add_argument("--host", default="")
    p_upd.add_argument("--user", default="")
    p_upd.add_argument("--port", type=int, default=0)
    p_upd.add_argument(
        "--os",
        default=None,
        choices=["unix", "linux", "macos", "windows"],
        help="Remote OS; omit to leave unchanged",
    )
    p_upd.add_argument(
        "--password",
        default="",
        help="Replace vault password (xor --key-file); prefer --password-prompt",
    )
    p_upd.add_argument(
        "--password-prompt",
        action="store_true",
        help="Read new password from terminal (not shown)",
    )
    p_upd.add_argument(
        "--key-file",
        default="",
        help="Replace vault private key (xor password)",
    )

    p_list = sub.add_parser("resource-list", help="List resources")
    p_list.add_argument("--kind", default="all")

    p_run = sub.add_parser("run", help="Run command on SSH resource")
    p_run.add_argument("--name", required=True)
    p_run.add_argument("--command", required=True)
    p_run.add_argument("--confirm-write", action="store_true")
    p_run.add_argument(
        "--foreground",
        action="store_true",
        help="Wait until command finishes (default: background)",
    )
    p_run.add_argument(
        "--await-probe",
        default="",
        help='JSON probe after session OK, e.g. {"type":"tcp","target":"127.0.0.1:8080","timeout_sec":60}',
    )

    sub.add_parser(
        "start",
        help="Launch native tray/menu-bar app (macOS status item or Windows tray + panel)",
    )
    sub.add_parser(
        "menubar",
        help="Alias of: start (left-click panel, right-click menu)",
    )
    sub.add_parser(
        "ui",
        help="Alias of: start on macOS/Windows; else local UI server only (no browser)",
    )
    sub.add_parser("board", help="Alias of: ui")
    sub.add_parser(
        "ui-server",
        help="Dev: run embedded UI HTTP server in foreground (does not open browser)",
    )
    sub.add_parser("board-json", help="Print task board JSON (by_resource)")

    p_probe = sub.add_parser(
        "probe", help="L2 reachability probe (LAN SSH + cloud IPs)"
    )
    p_probe.add_argument("--kind", default="", help="ssh|oracle|gcp|aliyun|tencent|all")

    p_metrics = sub.add_parser(
        "metrics",
        help="L3 host metrics via SSH (cpu/load / mem / disk / uptime / net)",
    )
    p_metrics.add_argument(
        "--name", default="", help="SSH resource name (default: all SSH hosts)"
    )

    p_cloud_metrics = sub.add_parser(
        "cloud-metrics",
        help=(
            "Best-effort cloud vendor metrics when SSH not linked "
            "(Aliyun/Oracle/Tencent/GCP → cpu/net; GCP mem needs Ops Agent)"
        ),
    )
    p_cloud_metrics.add_argument(
        "--cloud",
        default="",
        choices=["oracle", "gcp", "aliyun", "tencent"],
        help="Limit to one cloud (default: all synced instances; unsupported → skipped)",
    )
    p_cloud_metrics.add_argument(
        "--name",
        default="",
        help="Single cloud resource name (must already be synced)",
    )

    p_auto = sub.add_parser(
        "autostart",
        help="Login autostart (macOS LaunchAgent / Windows Startup folder): status / enable / disable",
    )
    auto_g = p_auto.add_mutually_exclusive_group()
    auto_g.add_argument(
        "--enable", action="store_true", help="Enable login / Startup autostart"
    )
    auto_g.add_argument(
        "--disable", action="store_true", help="Disable login / Startup autostart"
    )

    p_install = sub.add_parser(
        "install-menubar",
        help=(
            "Refresh native launcher (apps/Grip.app or apps/Grip.cmd); optionally "
            "install to Applications / Start Menu and/or enable login autostart"
        ),
    )
    p_install.add_argument(
        "--applications",
        action="store_true",
        help="macOS: symlink into ~/Applications; Windows: copy into Start Menu Programs",
    )
    p_install.add_argument(
        "--autostart",
        action="store_true",
        help="Enable login autostart after refresh/install",
    )
    p_install.add_argument(
        "--applications-dir",
        default="",
        help="Override Applications / Start Menu directory",
    )

    p_package = sub.add_parser(
        "package-menubar",
        help=(
            "Write dist zip with refreshed launcher + README "
            "(macOS unsigned .app / Windows Grip.cmd)"
        ),
    )
    p_package.add_argument(
        "--dist-dir",
        default="",
        help="Output directory for the zip (default: <repo>/dist)",
    )

    p_tun = sub.add_parser(
        "tunnel",
        help="LAN-first self-hosted tunnel (per-device Node; no Gateway jump host)",
    )
    tun_sub = p_tun.add_subparsers(dest="tunnel_cmd", required=True)
    tun_sub.add_parser("init", help="Create local operator WireGuard identity")
    tun_sub.add_parser("status", help="Show rendezvous / peers")
    p_tun_rv = tun_sub.add_parser(
        "rendezvous",
        help="Run self-hosted rendezvous (HTTP/TLS + TCP relay + STUN)",
    )
    p_tun_rv.add_argument("--bind", default="127.0.0.1:443", help="host:port")
    p_tun_rv.add_argument(
        "--token",
        default="",
        help="Shared auth token (required for register/relay when set)",
    )
    p_tun_rv.add_argument("--tls-cert", default="", help="TLS certificate PEM path")
    p_tun_rv.add_argument("--tls-key", default="", help="TLS private key PEM path")
    p_tun_rv.add_argument(
        "--tls-selfsigned",
        action="store_true",
        help="Generate ephemeral self-signed TLS cert (dev / quick HTTPS)",
    )
    p_tun_set = tun_sub.add_parser("set-rendezvous", help="Save rendezvous URL")
    p_tun_set.add_argument("url")
    p_tun_set.add_argument(
        "--token",
        default="",
        help="Shared auth token for this rendezvous (stored in settings)",
    )
    p_tun_enroll = tun_sub.add_parser(
        "enroll",
        help="On LAN: SSH-install Node on a device so it dials your rendezvous",
    )
    p_tun_enroll.add_argument("--name", required=True, help="SSH resource name")
    p_tun_enroll.add_argument(
        "--rendezvous-url",
        default="",
        help="Override saved rendezvous URL for this enroll",
    )
    p_tun_node = tun_sub.add_parser(
        "node", help="Run device Node in foreground (uses ~/.grip-node/config.json)"
    )
    p_tun_node.add_argument(
        "--config",
        default="",
        help="Path to node config.json (default: ~/.grip-node/config.json)",
    )
    p_tun_dis = tun_sub.add_parser("disable", help="Disable tunnel for SSH resource")
    p_tun_dis.add_argument("--name", required=True)
    # Deprecated stubs
    tun_sub.add_parser("gateway", help="(removed) use per-device enroll/node instead")
    p_tun_en = tun_sub.add_parser("enable", help="(removed) use: grip tunnel enroll --name")
    p_tun_en.add_argument("--name", default="")
    p_tun_en.add_argument("--gateway", default="")

    p_watch = sub.add_parser(
        "watch", help="Loop: optional sync-all + probe + metrics"
    )
    p_watch.add_argument("--interval", type=int, default=60)
    p_watch.add_argument(
        "--sync",
        action="store_true",
        help="Also run cloud L1 sync each tick",
    )
    p_watch.add_argument(
        "--metrics",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include L3 SSH metrics each tick (default: on; use --no-metrics to skip)",
    )
    p_watch.add_argument("--once", action="store_true", help="Single tick then exit")

    sub.add_parser("task-list", help="List recent tasks")
    p_task = sub.add_parser("task-get", help="Get task by id")
    p_task.add_argument("task_id")

    p_audit = sub.add_parser(
        "audit-export",
        help="Export redacted execution audit trail (one device or all)",
    )
    p_audit.add_argument(
        "--name",
        default="",
        help="Device/resource name (default: all devices)",
    )
    p_audit.add_argument(
        "--format",
        default="jsonl",
        choices=["jsonl", "json", "csv"],
        help="Export format (default: jsonl)",
    )
    p_audit.add_argument(
        "--out",
        default="",
        help="Write to file (default: stdout)",
    )
    p_audit.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="Max rows (default 2000, max 10000)",
    )

    p_sync = sub.add_parser("sync", help="Sync one cloud via bundled SDK")
    p_sync.add_argument("cloud")
    sub.add_parser("sync-all", help="Sync all configured clouds")
    sub.add_parser("oracle-sync", help="Alias: sync oracle")

    sub.add_parser("status-write", help="Rewrite ~/.grip/status.json")

    args = parser.parse_args(argv)
    ensure_home()
    svc = GripService()

    if args.cmd == "mcp":
        from grip.mcp_server import main as mcp_main

        mcp_main()
        return 0

    if args.cmd == "cloud-add":
        if not args.region:
            args.region = {
                "aliyun": ["cn-hangzhou"],
                "tencent": ["ap-guangzhou"],
                "oracle": ["ap-tokyo-1"],
                "gcp": [],
            }.get(args.cloud, [])
        print(json.dumps(_add_cloud(svc, args), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "cloud-list":
        print(json.dumps(svc.list_cloud_accounts(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "resource-add-ssh":
        password = args.password or None
        if args.password_prompt:
            import getpass

            password = getpass.getpass("SSH password: ")
        key_file = Path(args.key_file) if args.key_file else None
        if key_file and password:
            print("use only one of --key-file or --password/--password-prompt", file=sys.stderr)
            return 2
        if not key_file and not password:
            print("need --key-file or --password / --password-prompt", file=sys.stderr)
            return 2
        r = svc.resources.add_ssh(
            name=args.name,
            host=args.host,
            user=args.user,
            key_file=key_file,
            password=password,
            port=args.port,
            os_name=args.os,
        )
        svc.maybe_detect_ssh_os(r.name)
        r = svc.resources.get(r.name) or r
        print(json.dumps(r.summary(), ensure_ascii=False, indent=2))
        svc.refresh_status()
        return 0

    if args.cmd == "resource-update-ssh":
        password = args.password or None
        if args.password_prompt:
            import getpass

            password = getpass.getpass("SSH password: ")
        key_file = Path(args.key_file) if args.key_file else None
        if key_file and password:
            print("use only one of --key-file or --password/--password-prompt", file=sys.stderr)
            return 2
        kw: dict = {}
        if (args.host or "").strip():
            kw["host"] = args.host.strip()
        if (args.user or "").strip():
            kw["user"] = args.user.strip()
        if args.port and int(args.port) > 0:
            kw["port"] = int(args.port)
        if args.os:
            kw["os_name"] = args.os
        if password:
            kw["password"] = password
        if key_file:
            kw["key_file"] = key_file
        if not kw:
            print(
                "nothing to update: pass --host/--user/--port/--os and/or credentials",
                file=sys.stderr,
            )
            return 2
        try:
            r = svc.resources.update_ssh(args.name, **kw)
        except KeyError:
            print(json.dumps({"error": "resource_not_found", "name": args.name}))
            return 1
        except (ValueError, FileNotFoundError, OSError) as exc:
            print(json.dumps({"error": str(exc), "name": args.name}))
            return 1
        print(json.dumps(r.summary(), ensure_ascii=False, indent=2))
        svc.refresh_status()
        return 0

    if args.cmd == "resource-list":
        print(json.dumps(svc.list_resources(kind=args.kind), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "run":
        probe = json.loads(args.await_probe) if args.await_probe else None
        out = svc.run_command(
            args.name,
            args.command,
            confirm_write=args.confirm_write,
            background=not args.foreground,
            await_probe=probe,
            actor="cli",
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("status") in {"success", "denied", "running"} else 1

    if args.cmd == "audit-export":
        out = svc.export_audit(
            name=(args.name or "").strip() or None,
            fmt=args.format,
            limit=args.limit,
        )
        if out.get("error"):
            print(json.dumps(out, ensure_ascii=False), file=sys.stderr)
            return 1
        body = out.get("body") or ""
        dest = (args.out or "").strip()
        if dest:
            Path(dest).expanduser().write_text(body, encoding="utf-8")
            print(
                json.dumps(
                    {
                        "ok": True,
                        "path": str(Path(dest).expanduser()),
                        "filename": out.get("filename"),
                        "count": out.get("count"),
                        "format": out.get("format"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            sys.stdout.write(body)
            if body and not body.endswith("\n"):
                sys.stdout.write("\n")
        return 0

    if args.cmd in {"start", "menubar", "ui", "board"}:
        if sys.platform == "darwin":
            import os

            # Prefer LaunchServices .app so the status item actually appears
            # in the macOS menu bar (bare python processes often do not).
            if os.environ.get("GRIP_MENUBAR_DIRECT") != "1":
                app = (
                    Path(__file__).resolve().parents[2] / "apps" / "Grip.app"
                )
                if app.is_dir():
                    import subprocess

                    subprocess.Popen(
                        ["open", str(app)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    print(
                        "Launched Grip.app — look top-right for "
                        "shipping-box icon + text “Grip” "
                        "(if missing, click the « menu-bar overflow)."
                    )
                    return 0

            from grip.native_app import run_native_menubar

            run_native_menubar()
            return 0
        if sys.platform == "win32":
            import os

            if os.environ.get("GRIP_MENUBAR_DIRECT") != "1":
                launcher = (
                    Path(__file__).resolve().parents[2] / "apps" / "Grip.cmd"
                )
                if launcher.is_file():
                    import subprocess

                    subprocess.Popen(
                        ["cmd.exe", "/c", str(launcher)],
                        cwd=str(launcher.parent.parent),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    print(
                        "Launched Grip tray — look near the clock for the Grip icon "
                        "(left-click opens panel)."
                    )
                    return 0

            from grip.native_app import run_native_menubar

            run_native_menubar()
            return 0
        from grip.web.app import run_ui

        print(
            "Native tray/menu-bar requires macOS or Windows; starting local UI "
            "server (open the URL in a browser yourself if needed).",
            file=sys.stderr,
        )
        run_ui(open_browser=False)
        return 0

    if args.cmd == "ui-server":
        from grip.web.app import run_ui

        run_ui(open_browser=False)
        return 0

    if args.cmd == "board-json":
        print(json.dumps(svc.board(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "probe":
        kind = args.kind or None
        if kind == "all":
            kind = None
        out = svc.probe_resources(kind=kind)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("status") == "success" else 1

    if args.cmd == "metrics":
        name = (args.name or "").strip() or None
        out = svc.sample_metrics(name=name)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        if out.get("error"):
            return 1
        return 0 if int(out.get("ok") or 0) > 0 or int(out.get("checked") or 0) == 0 else 1

    if args.cmd == "cloud-metrics":
        cloud = (args.cloud or "").strip() or None
        name = (args.name or "").strip() or None
        out = svc.sample_cloud_metrics(cloud=cloud, name=name)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        if out.get("error"):
            return 1
        if out.get("status") == "skipped":
            return 0
        return 0 if int(out.get("ok") or 0) > 0 or int(out.get("checked") or 0) == 0 else 1

    if args.cmd == "autostart":
        from grip import autostart

        try:
            if args.enable:
                out = autostart.enable()
            elif args.disable:
                out = autostart.disable()
            else:
                out = autostart.status()
        except FileNotFoundError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "install-menubar":
        from grip.install_menubar import install_menubar

        apps_dir = (
            Path(args.applications_dir).expanduser()
            if (args.applications_dir or "").strip()
            else None
        )
        try:
            out = install_menubar(
                applications=bool(args.applications),
                autostart_enable=bool(args.autostart),
                applications_dir=apps_dir,
            )
        except (FileNotFoundError, IsADirectoryError, OSError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(out, ensure_ascii=False, indent=2))
        apps = out.get("applications") or {}
        if apps and not apps.get("ok"):
            return 1
        return 0

    if args.cmd == "package-menubar":
        from grip.install_menubar import package_menubar

        dist_dir = (
            Path(args.dist_dir).expanduser()
            if (args.dist_dir or "").strip()
            else None
        )
        try:
            out = package_menubar(dist_dir=dist_dir)
        except OSError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("ok") else 1

    if args.cmd == "tunnel":
        from grip.tunnel.ops import (
            disable_resource_tunnel,
            set_rendezvous_url,
            tunnel_status,
        )
        from grip.tunnel.store import TunnelStore

        store = TunnelStore()
        tcmd = args.tunnel_cmd
        if tcmd in {"gateway", "enable", "export-pair", "import-pair"}:
            print(
                json.dumps(
                    {
                        "error": "gateway_model_removed",
                        "hint": "Use per-device Node: grip tunnel set-rendezvous … && grip tunnel enroll --name <device>",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        if tcmd == "init":
            ident = store.ensure_identity(role="client")
            print(
                json.dumps(
                    {
                        "ok": True,
                        "node_id": ident["node_id"],
                        "public_key": ident["public_key"],
                        "model": "node",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if tcmd == "status":
            print(json.dumps(tunnel_status(store), ensure_ascii=False, indent=2))
            return 0
        if tcmd == "set-rendezvous":
            print(
                json.dumps(
                    set_rendezvous_url(
                        args.url,
                        store,
                        token=(args.token or "").strip() or None,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if tcmd == "enroll":
            from grip.tunnel.enroll import enroll_resource

            try:
                out = enroll_resource(
                    svc.resources,
                    svc.vault,
                    args.name,
                    rendezvous_url=(args.rendezvous_url or "").strip() or None,
                    store=store,
                )
            except (KeyError, ValueError) as exc:
                print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
                return 1
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if out.get("ok") else 1
        if tcmd == "node":
            from grip.tunnel.node import run_node_from_config_file

            cfg_path = Path(args.config).expanduser() if (args.config or "").strip() else None
            print(
                json.dumps(
                    {"ok": True, "hint": "Node running; Ctrl+C to stop"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            try:
                run_node_from_config_file(cfg_path)
            except KeyboardInterrupt:
                return 0
            return 0
        if tcmd == "disable":
            try:
                out = disable_resource_tunnel(svc.resources, args.name)
            except KeyError as exc:
                print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
                return 1
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if tcmd == "rendezvous":
            from grip.tunnel.rendezvous import start_rendezvous

            bind = (args.bind or "127.0.0.1:443").strip()
            host, _, port_s = bind.partition(":")
            port = int(port_s or 443)
            httpd, url = start_rendezvous(
                host=host or "127.0.0.1",
                port=port,
                background=False,
                token=(args.token or "").strip() or None,
                tls_cert=(args.tls_cert or "").strip() or None,
                tls_key=(args.tls_key or "").strip() or None,
                tls_self_signed=bool(args.tls_selfsigned),
            )
            bound_port = int(httpd.server_address[1])
            print(
                json.dumps(
                    {
                        "ok": True,
                        "url": url,
                        "relay_tcp_port": bound_port,
                        "stun_udp_port": bound_port,
                        "auth_required": bool((args.token or "").strip()),
                        "hint": "Ctrl+C to stop; clients: grip tunnel set-rendezvous URL --token …",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                httpd.shutdown()
            return 0
        print(json.dumps({"error": f"unknown tunnel cmd {tcmd}"}, ensure_ascii=False))
        return 2

    if args.cmd == "watch":
        import time

        def tick():
            out = svc.watch_once(sync=args.sync, metrics=args.metrics)
            print(json.dumps(out, ensure_ascii=False))

        if args.once:
            tick()
            return 0
        print(
            f"watching every {args.interval}s (sync={args.sync}, metrics={args.metrics}); Ctrl+C to stop",
            file=sys.stderr,
        )
        while True:
            tick()
            time.sleep(max(5, args.interval))


    if args.cmd == "task-list":
        print(json.dumps(svc.list_tasks(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "task-get":
        print(json.dumps(svc.get_task(args.task_id), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "sync":
        out = svc.sync_cloud(args.cloud)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("status") == "success" else 1

    if args.cmd == "sync-all":
        out = svc.sync_all()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("status") == "success" else 1

    if args.cmd == "oracle-sync":
        out = svc.sync_cloud("oracle")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("status") == "success" else 1

    if args.cmd == "status-write":
        print(str(write_snapshot(svc.resources, svc.tasks)))
        return 0

    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
