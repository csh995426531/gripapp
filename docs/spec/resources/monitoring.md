# Resource Monitoring vs Task Monitoring

## 两层不要混

| 层 | 问题 | 数据从哪来 | Grip 怎么呈现 |
|----|------|------------|---------------|
| **任务监控** | 这次 MCP 发出的操作跑完没有？ | 执行通道会话 + 可选 probe | board / get_task |
| **资源监控** | 这台机器/实例**现在**是否在、是否健康？ | 云 API 或对本机探测 | resource status / sync / probe / 看板资源条 |

---

## 怎么做（落地路径）

### L1 控制面 — 云

* `grip sync` / `sync-all` / MCP `sync_cloud`：bundled SDK 拉实例 lifecycle  
* **合理且已实现**

### L2 可达性 — 云 IP + 局域网

* `grip probe` / MCP `probe_resources`：对 SSH `host:port`、云实例 `public_ip`/`private_ip` 做 TCP  
* stopped 等控制面状态跳过 L2  
* `grip watch [--sync] [--no-metrics] [--interval 60]`：周期 L1（可选）+ L2 + L3（默认开，`--no-metrics` 关）

### L3 主机指标（SSH 轻量采样）

* 对已登记 **SSH** 设备：不装 Agent，远程跑只读脚本采集常见指标  
  * cpu_used_pct · mem_used_pct · disk_used_pct（列表）  
  * load1 · uptime · net_rx/tx_kbps（详情补充）  
* CLI/API/MCP：`sample_metrics`；面板列表/详情展示；`watch_once` 默认一并采样  
* Windows SSH（`meta.os=windows`）：远程 PowerShell/`Get-CimInstance`，输出同款 `key=value`  
  * 有：cpu_used_pct · mem_* · disk_*（最大固定盘）· uptime · net_*（perf 计数器，尽力）  
  * 无：load1（Windows 无 Unix load average，不伪造）  
* **OS 自动探测**：`meta.os` 缺失或为默认 `unix` 时，采样前（及添加后）走廉价 SSH 探测：`uname -s` → Linux/macOS；失败再试 PowerShell `$env:OS` → Windows。成功则 `patch_meta` 写入 `os`（不擦其它 meta）；失败则保持原 meta，指标按现状优雅失败。面板添加/编辑可选 unix/windows 覆盖。  
* 云厂商监控（无 Agent）：**Partial** — Aliyun ECS `DescribeInstanceMonitorData` → `cpu_used_pct`（`grip cloud-metrics` / MCP `sample_cloud_metrics`）；无 memory%。Tencent/OCI/GCP 利用率与常驻 Agent 仍后置。Sync 时 Tencent/Aliyun 可带容量字段（`cpu_cores` / `mem_gb`·`mem_mb`），不是利用率。

### 看板

* 上方：资源 chips（status）  
* 下方：按设备分列的任务（session status）  

---

## 命令 / MCP

| 动作 | CLI | MCP |
|------|-----|-----|
| 云清单 L1 | `grip sync-all` | `sync_all` |
| 可达性 L2 | `grip probe` | `probe_resources` |
| L3 指标 | `grip metrics [--name]` | `sample_metrics`（含 net rx/tx；无额外 flag） |
| 云厂商指标 | `grip cloud-metrics [--cloud] [--name]` | `sample_cloud_metrics`（Aliyun/Oracle/Tencent CPU；未开通 SSH 时；GCP skipped） |
| SSH 编辑 | `grip resource-update-ssh` | `update_ssh`（不回显密钥） |
| 开机自启 | `grip autostart [--enable\|--disable]` | `autostart_status` / `set_autostart` |
| 一轮巡检 | `grip watch --once [--sync] [--no-metrics]` | `watch_once` (`sample_metrics` default true) |
| 看板 | `grip board` | `board` |

---

## 实现对照

| 能力 | 现状 |
|------|------|
| 云 L1 SDK sync | ✅ |
| 任务会话监控 | ✅ |
| L2 TCP probe | ✅ `monitor.py` |
| watch 循环 | ✅ |
| L3 SSH 轻量指标 | ✅ Unix CPU/mem/disk/net/load；Windows 同形（无 load1） |
| SSH OS 自动探测 | ✅ `detect_remote_os` → 持久化 `meta.os`；避免默认 unix 跑错脚本 |
| 云监控 API / Agent | Partial：Aliyun CPU；其它 + Agent 后置 |
