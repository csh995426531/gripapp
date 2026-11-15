# Grip Product Spec (Full Product)

## One-liner

Local **secure MCP resource gateway**: install Grip once; thin HTTPS clients talk to Oracle / GCP / Aliyun / Tencent; AI/human operate via **MCP**; secrets stay in local vault; menubar shows resources + task progress.

## Hard requirements

1. **No external cloud CLIs or vendor SDKs** (`oci` / `gcloud` / `aliyun` / `tccli` not needed). Only the inventory + monitor HTTPS APIs Grip uses.
2. **Credentials** entered via `grip cloud-add` / operator UI → encrypted vault. MCP never returns keys.
3. **MCP is the operation entry** for local AI (and any MCP client). Humans also use CLI/menubar for setup & watch.
4. **Task monitoring**: every MCP `run_command` / `sync_*` creates a task with lifecycle + redacted summary; menubar shows them.
5. **Resource monitoring**: cloud L1 via SDK sync; L2 TCP probe for LAN + cloud IPs (`probe` / `watch`); L3 SSH metrics Done (CPU/mem/disk/net/load; Windows: no load1, disk/net best-effort).
6. **LAN SSH**: register hosts; AI runs commands by logical name through MCP.

## Clouds

| Cloud | Bundled SDK | Credential in vault |
|-------|-------------|---------------------|
| Oracle | Compute + Monitoring REST | user/tenancy/fingerprint/private_key + compartment |
| GCP | Compute + Monitoring REST | service account JSON |
| Aliyun | ECS RPC (list + monitor) | AccessKey Id/Secret |
| Tencent | CVM/Lighthouse + CMS | SecretId/SecretKey |

## Out of scope

Bastion / session recording / multi-user RBAC / multi-tenant SaaS.
