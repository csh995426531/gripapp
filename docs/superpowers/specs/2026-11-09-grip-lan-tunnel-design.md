# Grip LAN-first tunnel — per-device Node (no Gateway)

Date: 2026-11-09  
Status: active (supersedes Gateway-centric draft)

## Problem

LAN SSH works at home; after leaving, `192.168.x` dies. Keep the same Grip device name working **without third-party mesh SaaS** (no Tailscale control plane).

## Decision

**No Home Gateway jump host.**  
Each enrolled SSH device runs a **Grip Node** that dials out to the user’s self-hosted **rendezvous**. The operator Mac is also a node/client.

Off-LAN order:

1. **WireGuard 直连**（优先加速）— 内嵌 `wireguard-go`；macOS 无权限时**弹一次管理员授权**拉起 utun；Linux enroll 尝试 `setcap cap_net_admin`  
2. **TCP 中继**（可靠保底）— 直连失败（权限拒绝 / 打洞失败 / TUN 不可用）时明确走汇合点中继，面板显示「中继」而非假直连

## Rendezvous (still required)

One install on infrastructure the user controls:

- User-owned VPS, or
- Home public IP / DDNS exposing the rendezvous ports

Nodes **dial out** to rendezvous (NAT-friendly). Devices do not need inbound ports from the internet.

If rendezvous is unset and LAN is down → fail with `tunnel_no_rendezvous` (never fake success).

## Bootstrap (LAN-first)

1. Add device with LAN IP (existing Grip SSH).
2. On LAN, `grip tunnel enroll --name <device>`:
   - SSH into the device
   - Install a lightweight Node (config + runner + user service / nohup)
   - Node registers at rendezvous and stays up across reboot when service install succeeds
3. Later, off-LAN: Grip probes LAN → fail → open relay/tunnel to that device’s Node → SSH as usual by logical name.

## Components

| Piece | Role |
|-------|------|
| Rendezvous | Self-hosted; peer register/lookup; observed endpoint hint; TCP relay fallback |
| Device Node | WG peer + dials rendezvous; bridges relay → `127.0.0.1:22` when direct down |
| Operator Grip | Path select (lan → **wg direct** → relay); enroll; board path badge |
| WireGuard | `10.77.0.0/24`; operator `.1`; devices `.2+`; **embedded `wireguard-go`** (UAPI, no system `wg-quick`); system tools only as optional fallback |

**Deprecated:** Home Gateway as the way to reach other LAN devices. (`grip tunnel gateway` may remain briefly as a warning stub.)

## Path selection

1. If `lan_host:port` is **not** in a silence window → probe it
2. If open → clear silence → `path=lan` (always preferred)
3. If probe fails → **silence** that LAN address for ~60s (skip re-probe on every call)
4. While silenced / off-LAN: try **WireGuard direct** to device `tunnel_ip` → `path=tunnel`
5. If WG down / tools missing: **rendezvous TCP relay** → `path=relay` (Node probe independent of LAN fail)
6. After silence expires → re-probe LAN:
   - open → switch back to `path=lan` immediately
   - fail → re-silence only; **do not** degrade WG/relay reachability
7. Else → `path=down` (`tunnel_no_rendezvous` / `tunnel_unavailable`)

Silence is in-memory per `host:port`.

## Resource meta (public)

```json
{
  "host": "192.168.5.15",
  "lan_host": "192.168.5.15",
  "port": 22,
  "tunnel": {
    "enabled": true,
    "node_id": "node-ab12cd34",
    "rendezvous_url": "http://vps:7700",
    "path": "lan|tunnel|relay|down"
  }
}
```

## CLI

- `grip tunnel set-rendezvous <url> [--token SECRET]`
- `grip tunnel rendezvous --bind … [--token SECRET] [--tls-cert/--tls-key | --tls-selfsigned]`
  — HTTP(S) + TCP relay (port+1) + STUN UDP (port+2)
- `grip tunnel enroll --name <ssh>` — LAN install/start Node (+ gzip’d embedded wireguard-go)
- `grip tunnel node` — run Node in foreground (debug / already on device)
- `grip tunnel status`
- `grip tunnel disable --name <ssh>`

Public rendezvous: always set `--token`; prefer TLS.

## Acceptance

- On LAN: unchanged.
- Off LAN: enrolled device reachable by same name via its Node + self-hosted rendezvous.
- Device without Node / rendezvous down: explicit error, not silent LAN “reachable”.
