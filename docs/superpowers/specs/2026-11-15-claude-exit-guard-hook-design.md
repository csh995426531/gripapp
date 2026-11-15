# Claude 出口守卫 Hook — 设计方案

日期：2026-11-15  
状态：已落地 B（2026-11-15）— **网络层 SOCKS 守卫**  
范围：仅 Clash 配置「彰化PQS-Claude」；双账号可切配置 / `ENABLED=0` 关闭检测

## 已确认决策

1. 出口：只要 **台湾（TW）** 即可  
2. DNS 失败：硬拦（配置 + 出口路径）  
3. **不使用 Claude Hooks**（防服务端/模型感知）  
4. **仅绑定彰化 profile**；其他地区账号切走 Clash 配置后自动不走守卫  
5. 另提供 `ENABLED` 总开关（不必做独立 App；需要时再加菜单栏）

## 控制面：Clash 配置 ↔ 检测策略

总控表：`~/.claude/hooks/policies.yaml`  
CLI：`~/.claude/hooks/claude-exit-guard-ctl`

| 命令 | 作用 |
|------|------|
| `list` | 列出 Clash 配置与 mode/region/account |
| `current` | 当前 Clash 配置生效的策略 |
| `set <uid\|name> mode=enforce\|bypass ...` | 改某配置策略 |
| `sync` | 从 Clash 同步未登记配置（默认 bypass） |
| `on` / `off` | 全局开关 |
| `status` | 状态 + 即时检测 |

双账号：台湾 → 彰化配置（enforce TW）；另一地区 → dmit/其他（bypass）。在 Clash 里切配置即可，策略表负责对应关系。


## 网络层链路（方案 B）

```
App/TUN → Clash(仅彰化 profile 的 Claude 规则)
       → socks 127.0.0.1:17999（守卫进程）
       → 检测通过？
            ├─ 否：断开（Claude 只见网络失败）
            └─ 是 → socks 127.0.0.1:17998（claude-pass listener）
                 → 东京中转-彰化落地 → 彰化
```

检测只走 17998，避免环路。连接级缓存默认 15s。

## 1. 目标

在 Claude Code **真正向 Anthropic 发请求 / 执行工具之前**，先跑一遍本地出口检测：

- **通过**：正常继续
- **不通过**：阻断本轮，并给出可读告警（原因 + 当前实测值 + 期望值）

要解决的问题：Clash 分流失效、节点挂了、DNS/UDP 漏回国内或东京直出时，Claude 仍在「脏出口」上跑而不自知。

## 2. 非目标

| 不做 | 原因 |
|------|------|
| Claude Desktop App 硬拦 | 无官方等价 Hook，拦不了应用层 API 包 |
| 复刻 Trust Score / VPN·滥用库 | 依赖对方私有风控库 |
| 浏览器级 WebRTC/Canvas | CLI 环境无完整浏览器栈 |
| 替代 Clash 分流本身 | Hook 只做门禁，不负责路由 |

## 3. 总体架构

```
你回车提交 Prompt
        │
        ▼
┌───────────────────────┐
│ UserPromptSubmit Hook │  ← 主闸门（可硬拦本轮 Prompt）
│  → check-claude-exit  │
└───────────┬───────────┘
            │ pass / fail
     ┌──────┴──────┐
     ▼             ▼
  继续会话      deny + 告警文案
                   │
                   ▼
            （可选）macOS 通知

工具调用前（可选第二道）
        │
        ▼
┌──────────────────┐
│ PreToolUse Hook  │  ← 防「上一轮过了、中途节点挂了」
│  → 同一检测脚本   │     （带短缓存，避免每次工具都全量测）
└──────────────────┘

会话开始
        │
        ▼
┌──────────────────┐
│ SessionStart     │  ← 只注入状态到上下文 / 打印提示，不能硬拦会话
│  → 检测（软提示） │
└──────────────────┘
```

检测逻辑抽成独立 CLI，Hook 只负责调它并翻译成 Claude Code 的 deny/allow。

## 4. 组件与文件

| 路径 | 职责 |
|------|------|
| `~/.claude/hooks/claude-exit-guard.sh`（或 `.py`） | 检测入口；读配置、跑检查、exit 0/2、stdout JSON/文案 |
| `~/.claude/hooks/claude-exit-guard.env` | 期望出口、超时、缓存秒数、是否硬拦 |
| `~/.claude/hooks/cache/last-ok.json` | 最近一次成功结果缓存 |
| `~/.claude/settings.json` → `hooks` | 挂 `UserPromptSubmit` / `PreToolUse` / `SessionStart` |

可选（后续）：把同一检测脚本挂到本机菜单栏 / Grip，手动点测；首版不做。

## 5. 通过标准（Pass Criteria）

默认按当前「彰化 PQS · Claude」链路设计，均可配置。

| # | 检查项 | 方法（本地） | 默认期望 | 失败即拦？ |
|---|--------|--------------|----------|------------|
| A | Clash / mihomo 存活 | Unix socket `/tmp/verge/verge-mihomo.sock` 或 `127.0.0.1:9097` | HTTP 通 | 是 |
| B | Claude 节点延迟 | API `GET /proxies/东京中转-彰化落地/delay` | delay &lt; 3000ms 且无 error | 是 |
| C | Claude 出口 IP | 经该节点测 `https://api.ipify.org`（临时 GLOBAL/或 mixed + 选节点）；或 `curl` 命中会走 `🟣 Claude` 的探测域名 | 等于配置的 `EXPECTED_IP`（默认 `111.252.180.101`），或 geo=TW 且 ASN 白名单 | 是 |
| D | DNS 出口 | 经代理解析/查询中华电信或对比「系统 DNS 请求是否进 Claude 组」；简化：`dig +short` 某域名并检查解析器路径，或探测 `168.95.1.1` 连通走代理 | 非中国运营商 DNS；优先命中 TW/CHT | 可配置，默认 **是** |
| E | UDP / STUN 近似 | 可选：对配置的 STUN 做一次 UDP，看 mapped address | 与 C 同出口或同为 TW | 默认 **否**（仅告警），避免 UDP 不稳误杀 |
| F | Claude 站点可达 | `curl -m 8 -o /dev/null -w %{http_code}` → `claude.ai` / `anthropic.com` | 2xx/3xx | 默认 **是** |

**硬拦集合（默认）**：A ∧ B ∧ C ∧ F；（D 开）∧ D。  
E 失败只告警，不拦。

## 6. Hook 行为细节

### 6.1 UserPromptSubmit（主闸）

- 跑检测（命中成功缓存则跳过全量，见 §7）
- **通过**：exit 0；可选 `additionalContext` 一行：`[exit-guard] OK ip=… delay=…ms`
- **失败**：exit 2，stderr 人类可读告警；或 JSON `decision: "block"` + `reason`  
  → Claude Code **本轮 Prompt 不发给模型**

### 6.2 PreToolUse（可选加固）

- matcher：`.*` 或常见高危工具；首版建议仅 `Bash|Edit|Write|Agent`
- 只用 **缓存内成功** 放行；缓存过期或上次失败 → 再全量测；失败则 `permissionDecision: deny`

### 6.3 SessionStart（软提示）

- 跑一次检测；结果写入 `additionalContext`
- **不能**阻止开会话；用于一进门就看见红/绿状态

## 7. 缓存与性能

| 项 | 默认 |
|----|------|
| 成功缓存 TTL | 60s |
| 失败 | **不缓存**（或仅 5s 防抖），下次立刻重测 |
| 单次全量超时 | 12s（各项并行，总闸） |
| UserPromptSubmit timeout | settings 里设 15–20s，避免误杀慢网 |

并行：A/B/C/F（及 D）同时跑，全部完成或总超时。

## 8. 告警形态

失败时 stderr / reason 固定模板，例如：

```
[claude-exit-guard] BLOCKED
原因: Claude 出口 IP 不符
期望: 111.252.180.101 (TW/彰化)
实际: 151.145.68.4 (东京直出)
建议: 检查 Clash 是否为「彰化PQS-Claude」+ 规则模式；🟣 Claude → 东京中转-彰化落地
```

可选：`osascript` 弹 macOS 通知（`ALERT_NOTIFY=1`）。

## 9. 配置项（`claude-exit-guard.env`）

```bash
EXPECTED_IP=111.252.180.101
EXPECTED_COUNTRY=TW          # 可选：无精确 IP 时用 geo 兜底
PROXY_NAME=东京中转-彰化落地
CLASH_SOCK=/tmp/verge/verge-mihomo.sock
CACHE_TTL_SEC=60
HARD_BLOCK=1                 # 0=只告警不拦
CHECK_DNS=1
CHECK_UDP=0
NOTIFY=1
```

## 10. 与现有 Clash 三层分流的关系

| 流量 | Clash | Guard 是否关心 |
|------|-------|----------------|
| Claude 域名/进程 | → 彰化 | **是**（C/F） |
| DNS/STUN（已强制 Claude 组） | → 彰化 | **是**（D/E） |
| 其它境外 | → 东京直出 | 否（不应要求等于彰化） |
| 国内 | DIRECT | 否 |

Guard 只验证 **Claude 专用出口**，不要求「整机所有流量都是台湾」。

## 11. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 探测本身污染路径（MATCH 走东京） | 出口 IP 必须经「指定节点」或「命中 🟣 Claude 规则的域名」测，禁止裸 `api.ipify.org` 当 Claude 出口 |
| 每轮变慢 | 成功缓存 60s；检查并行 |
| 误杀（短暂抖动） | 延迟阈值放宽；失败可手动 `HARD_BLOCK=0`；提供 `claude-exit-guard.sh --once` 自检 |
| settings.json 已有其它 hooks | 合并追加，不覆盖用户已有项 |

## 12. 验收标准

1. 节点正常：连续提交 Prompt，不误拦；stderr/上下文可见 OK  
2. 关掉「东京中转-彰化落地」或切错 profile：下一轮 Prompt 被 block，告警含期望/实际  
3. 仅东京直出通、彰化挂：必须 block（不能只看到东京 IP 当成功）  
4. `HARD_BLOCK=0`：失败只告警，会话可继续  
5. `check --once` 可在终端单独跑，退出码 0/2 与 Hook 一致  

## 13. 实施顺序（确认后）

1. 实现 `claude-exit-guard` CLI + env + 缓存  
2. 合并写入 `~/.claude/settings.json` hooks  
3. 本地验收 §12  
4. （可选）macOS 通知、UDP 检查升级为硬拦  

## 14. 确认结果（2026-11-15）

1. 硬拦范围：**Prompt + 工具**  
2. 期望出口：**台湾（TW）** 即可  
3. DNS 失败：**硬拦**  
4. 配置：**全局 `~/.claude`**

### 落地位置

- `~/.claude/hooks/claude-exit-guard.py` — 本地检测（可单独跑）
- `~/.claude/hooks/claude-exit-guard.env`
- `~/.claude/hooks/claude-with-exit-guard` — **启动前**包装器（推荐用法）
- `~/.claude/settings.json` → `hooks: {}`（已清空，不注册）
- 自检：`python3 ~/.claude/hooks/claude-exit-guard.py --once`
- 启动：`~/.claude/hooks/claude-with-exit-guard`（或自己 alias 到它）
