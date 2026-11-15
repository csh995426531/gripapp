# Grip Acceptance

| Area | Status |
|------|--------|
| Task session lifecycle + heartbeat + probe + board | Done |
| Cloud L1 SDK sync (no external CLIs) | Done |
| Resource L2 TCP probe + watch | Done |
| Board: resources + tasks | Done |
| macOS menu-bar primary UX (NSStatusItem + popover, no Safari) | Done |
| Windows via OpenSSH (commands) | Done |
| L3 SSH metrics: CPU% / mem / disk (+ load/uptime/net in detail) | Done |
| SSH device edit in panel | Done |
| SSH update via MCP/CLI (`update_ssh` / `resource-update-ssh`) | Done |
| Login autostart (LaunchAgent) | Done |
| Autostart via MCP/CLI (`autostart_status`/`set_autostart` / `grip autostart`) | Done |
| Local menu-bar install (`grip install-menubar` / `scripts/install-menubar.sh`: refresh `apps/Grip.app`, optional `~/Applications` symlink + autostart) | Done |
| Windows SSH host metrics | Done (no load1; disk=largest fixed volume; net best-effort) |
| SSH remote OS auto-detect (`meta.os`) | Done (uname / PowerShell probe; persist on sample/add; panel override) |
| Cloud vendor metrics APIs / host agent | Done — Aliyun/Oracle/Tencent/GCP via `cloud-metrics` when SSH not linked (GCP: CPU + net; mem only if Ops Agent already reporting). Installing Ops Agent / host agents remains out of scope |
| Unsigned local/dist package (`grip package-menubar` → `dist/grip-menubar-unsigned-*.zip`; Gatekeeper will warn; needs clone + `.venv`) | Done |
| Standalone macOS DMG / Windows setup (`scripts/build_macos_installer.sh`, `scripts/build_windows_installer.ps1`) | Done (unsigned by default) |
| Signed installer / notarized DMG | Deferred — needs Apple Developer ID; `build_macos_installer.sh` supports optional `GRIP_CODESIGN_IDENTITY` + `GRIP_NOTARY_*` when credentials are present |
| LAN-first tunnel: per-device Node + self-hosted rendezvous (no Gateway) | Done |
| Tunnel path select: LAN → silence → WireGuard direct → TCP relay | Done |
| Embedded `wireguard-go` (no system `wg-quick`); macOS admin utun + privilege cooldown persist | Done |
| Rendezvous token auth + optional TLS + STUN whoami | Done |
| Enroll ships gzip’d wireguard-go; Windows enroll via OpenSSH/PowerShell | Done |
| Audit trail export (actor + JSONL/JSON/CSV; panel/CLI/MCP) | Done |
| Run gate: refuse/fail-fast on unreachable + stale running reap | Done |
| Per-host SSH concurrency cap (default 3, queue) + session reuse for metrics | Done |

```bash
cd grip && .venv/bin/pytest -q
```
