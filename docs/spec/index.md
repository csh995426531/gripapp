# Grip — Spec Index

**Product**: Local secure MCP resource gateway + task observability (menubar).  
**Package root**: `grip/`  
**Related PRD**: `.trellis-scratch/tasks/mac-menubar-multicloud-monitor/prd.md`  
**Canonical (versioned) copy**: this directory under `grip/docs/spec/` (preferred over ignored `.trellis/`).

## Pre-Development Checklist

Before coding any Grip change, read:

1. [`guides/pre-dev.md`](guides/pre-dev.md) — thinking checklist
2. [`mcp/contracts.md`](mcp/contracts.md) — MCP tool signatures & response contracts
3. [`security/secrets.md`](security/secrets.md) — vault + redaction rules (non-negotiable)
4. [`tasks/store.md`](tasks/store.md) — task lifecycle & persistence
5. [`resources/model.md`](resources/model.md) — resource identity & SSH/cloud inventory
6. [`resources/monitoring.md`](resources/monitoring.md) — **resource vs task monitoring**; cloud API vs LAN probe
7. [`menubar/ui-contract.md`](menubar/ui-contract.md) — menubar status surface
8. [`tasks/execution-status-sync.md`](tasks/execution-status-sync.md) — **how running/finished syncs** (process lifecycle, not business outcomes)

## Layers

| Layer | Path | Purpose |
|-------|------|---------|
| Product | [`product.md`](product.md) | Vision, MVP, non-goals |
| Architecture | [`architecture.md`](architecture.md) | Components, data flow |
| MCP | [`mcp/`](mcp/) | Tools, schemas, errors |
| Security | [`security/`](security/) | Vault, redaction, allowlist |
| Tasks | [`tasks/`](tasks/) | Job store & status |
| Resources | [`resources/`](resources/) | Inventory model |
| Menubar | [`menubar/`](menubar/) | Local UI contract |
| Guides | [`guides/`](guides/) | Pre-dev checklists |
| Tunnel | [`../superpowers/specs/2026-11-09-grip-lan-tunnel-design.md`](../superpowers/specs/2026-11-09-grip-lan-tunnel-design.md) | LAN-first self-hosted tunnel (no third-party mesh) |

## Design Decisions (summary)

1. **New package `grip/`** inside this workspace (not mixed into `eva_link/` app code). Inspired by noc-ops providers/sync patterns; not a blind full-tree vendor unless needed.
2. **MCP is the primary AI entry**; menubar is human observability.
3. **Secrets never appear in MCP tool results or task summaries** (redact + allowlist fields).
4. **Full product resources**: LAN SSH + Oracle + GCP + Aliyun + Tencent (CLI sync).
5. **Do not ship raw noc-ops `ops-mcp` as the product** — keep Grip redaction gateway + task/menubar board (see architecture “Relation to noc-ops”).
6. Scope is **full product**, not MVP-only — see `product.md`.
