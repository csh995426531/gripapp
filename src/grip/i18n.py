"""UI language. Default Chinese. English is selected in the panel."""

from __future__ import annotations

from typing import Any

from grip.paths import ensure_home

_LANGS = {"zh", "en"}

# Chinese source → English. Longer keys are applied first so 中继 does not
# eat 汇合点中继.
UI_EN: dict[str, str] = {
    "本机运维": "Local ops",
    "语言": "Language",
    "云账号": "Cloud accounts",
    "云": "Cloud",
    "＋ 设备": "+ Device",
    "设备": "Devices",
    "下次指标采样": "Next metrics sample",
    "切换列表布局": "Switch list layout",
    "两列": "Two columns",
    "单列": "One column",
    "切换为单列（带曲线）": "Switch to one column (with charts)",
    "切换为两列紧凑（无曲线）": "Switch to two compact columns",
    "导出全部设备执行日志": "Export logs for every device",
    "导出日志": "Export log",
    "点选设备查看指标与操作": "Select a device for metrics and actions",
    "左侧⋮拖动排序 · 点选查看指标与操作": "Drag ⋮ to reorder · select for metrics and actions",
    "两列总览 · 左侧⋮拖动排序 · 点选进详情": "Two columns · drag ⋮ to reorder · select for details",
    "加载中…": "Loading…",
    "正在读取本机设备列表": "Reading devices on this computer",
    "添加设备": "Add device",
    "还没有设备": "No devices yet",
    "先加一台 SSH 设备（局域网或云服务器）": "Add an SSH device (on the LAN or in the cloud)",
    "← 全部设备": "← All devices",
    "指标": "Metrics",
    "最近一次成功采样时间": "Last successful sample",
    "尚未采样": "No sample yet",
    "刷新": "Refresh",
    "操作": "Actions",
    "运行命令": "Run command",
    "执行": "Run",
    "在这台设备上执行只读命令；写操作需确认。": "Read-only commands run here. Writes need confirmation.",
    "最近任务": "Recent tasks",
    "导出本设备执行日志": "Export this device's log",
    "← 返回": "← Back",
    "编辑设备": "Edit device",
    "名称不可改。仅改备注性字段时，密码/私钥可留空保持原凭证；若改了主机/端口/用户，必须填新密码或私钥（否则会沿用上一台机器的密钥）。": "The name cannot change. Leave the password or key blank to keep the saved secret, unless you change the host, port, or user.",
    "系统": "System",
    "Linux / macOS（未细分）": "Linux / macOS (unspecified)",
    "名称": "Name",
    "主机": "Host",
    "用户名": "User",
    "端口": "Port",
    "认证": "Auth",
    "密码": "Password",
    "私钥文件": "Private key file",
    "私钥": "Private key",
    "内存": "Memory",
    "磁盘剩余": "Disk free",
    "磁盘": "Disk",
    "已运行": "Uptime",
    "网络": "Network",
    "负载": "Load",
    "采样失败: ": "Sample failed: ",
    "尚未采样 — 点右上角刷新": "No sample yet — use Refresh",
    "最近成功采样 ": "Last sample ",
    "采样 ": "Sample ",
    "编辑": "Edit",
    "复制 SSH": "Copy SSH",
    "刷新指标": "Refresh metrics",
    "认证 ": "Auth ",
    "展开": "Expand",
    "收起": "Collapse",
    "暂无任务": "No tasks yet",
    "无输出": "No output",
    "网络 当前 ": "Network now ",
    " · 近30分钟 ": " · last 30 min ",
    "网络恢复后指标会自己回来": "metrics come back after the network returns",
    "关联 SSH 凭证丢失，请编辑 SSH 设备重填": "Linked SSH secret is missing. Edit the SSH device and fill it in again",
    "保险库里没有这台设备的密码/私钥 — 点进去编辑并重填": "No password or key in the vault. Open the device and fill it in again",
    "凭证丢失（保险库重置后密码/私钥未重填）— 请点「编辑」重新保存密码或私钥后再执行": "Secret missing. Choose Edit, save the password or key again, then run the command.",
    "凭证丢失：请点「编辑」重新填写密码或私钥后保存": "Secret missing. Choose Edit and save the password or key again.",
    "错误: 凭证丢失 — 请编辑设备重填密码/私钥": "Error: secret missing. Edit the device and fill in the password or key again.",
    "设备不可达 — 请先探测/恢复网络后再执行命令（不会进入执行中）。": "The device is unreachable. Probe or restore the network before running a command.",
    "未开通 SSH · 可点刷新拉取 OCI 云侧 CPU/内存/负载/网络（需 Cloud Agent）；开通 SSH 后改走主机指标。": "SSH is not enabled. Refresh to pull OCI CPU, memory, load, and network. After SSH is enabled, metrics come from the host.",
    "未开通 SSH · 可点刷新拉取 GCP Monitoring CPU/网络（内存需 Ops Agent）；开通 SSH 后改走主机指标。": "SSH is not enabled. Refresh to pull GCP Monitoring CPU and network. After SSH is enabled, metrics come from the host.",
    "未开通 SSH · 可点刷新拉取腾讯云 CMS CPU/内存/网络；开通 SSH 后改走主机指标。": "SSH is not enabled. Refresh to pull Tencent Cloud CPU, memory, and network. After SSH is enabled, metrics come from the host.",
    "未开通 SSH · 可点刷新拉取 ECS 云侧 CPU/网络（此 API 无内存%）；开通 SSH 后改走主机指标。": "SSH is not enabled. Refresh to pull ECS CPU and network. After SSH is enabled, metrics come from the host.",
    "未开通 SSH · 暂无主机指标。": "SSH is not enabled. No host metrics yet.",
    "新密码（可选）": "New password (optional)",
    "新私钥文件（可选）": "New private key (optional)",
    "选择新私钥（留空保持原凭证）": "Choose a new key (blank keeps the current one)",
    "选择…": "Choose…",
    "保存": "Save",
    "← 取消": "← Cancel",
    "取消": "Cancel",
    "局域网或云上 SSH 主机均可。密码/密钥只进本机保险库，不会给 AI。": "LAN or cloud SSH hosts. Passwords and keys stay in the local vault and are not sent to the AI.",
    "192.168.5.15 或公网 IP": "192.168.5.15 or a public IP",
    "Linux / macOS / Windows OpenSSH 默认端口均为 22，切系统会自动填入；可再改。": "OpenSSH on Linux, macOS, and Windows defaults to port 22. Changing the system fills it in; you can edit it.",
    "选择本机私钥文件": "Choose a private key on this computer",
    "保存并探测": "Save and probe",
    "← 设备": "← Devices",
    "添加": "Add",
    "同步资源": "Sync resources",
    "点账号进入编辑（地域、密钥、实例 SSH）。保存后点「同步资源」拉取实例；账号设了实例 SSH 时会自动开通可执行设备。": "Select an account to edit region, keys, and instance SSH. Save, then sync to pull instances.",
    "← 云账号列表": "← Cloud accounts",
    "添加云账号": "Add cloud account",
    "编辑云账号": "Edit cloud account",
    "编辑 · ": "Edit · ",
    "ap-tokyo-1 或 cn-hangzhou": "ap-tokyo-1 or cn-hangzhou",
    "新建需填写厂商凭证。实例 SSH 可选，填了同步后会自动开通。": "A new account needs the vendor credentials. Instance SSH is optional; if you fill it in, sync opens SSH.",
    "普通字段直接改。仅「API 私钥 / AccessKey Secret / 服务账号 JSON / 实例登录私钥」留空表示不更换（实例登录私钥已保存）。": "Edit the fields directly. Leave the API private key, AccessKey Secret, service-account JSON, or instance login key blank to keep the current secret. The instance login key is already saved.",
    "普通字段直接改。仅「API 私钥 / AccessKey Secret / 服务账号 JSON / 实例登录私钥」留空表示不更换。": "Edit the fields directly. Leave the API private key, AccessKey Secret, service-account JSON, or instance login key blank to keep the current secret.",
    " API 凭证已保存。": " API credentials are saved.",
    "凭证库里的密钥已失效或丢失 — 请重新填写标识字段并选择 API 私钥后再保存。实例登录私钥如需开通 SSH 也请一并重选。": "The saved secret is missing or invalid. Fill in the identity fields and choose the API private key again, then save. Re-select the instance login key too if SSH should be opened.",
    "凭证库里的密钥已失效或丢失 — 请重新填写标识字段并选择 API 私钥后再保存。": "The saved secret is missing or invalid. Fill in the identity fields and choose the API private key again, then save.",
    "厂商": "Vendor",
    "阿里云": "Alibaba Cloud",
    "腾讯云": "Tencent Cloud",
    "备注名": "Label",
    "地域（可逗号分隔）": "Regions (comma-separated)",
    "地域": "Region",
    " · 已设私钥": " · key set",
    "未设实例 SSH": "No instance SSH",
    "不更换请留空": "Leave blank to keep the current secret",
    "产品": "Product",
    "项目 ID": "Project ID",
    "服务账号": "Service account",
    "服务账号 JSON": "Service account JSON",
    "API 私钥": "API private key",
    "实例 SSH 用户（可选，同步后自动开通 SSH）": "Instance SSH user (optional, opens SSH after sync)",
    "实例登录私钥（可选，不是 API 密钥）": "Instance login key (optional, not the API key)",
    "删除": "Delete",
    "确认": "Confirm",
    "需要授权后才能使用": "Permission required",
    "按下面两项授权即可进入。每项点「去授权」，在 macOS 弹窗里点允许。未签名安装包若钥匙串弹不出来，点左下角「改用文件保管」。": "Allow the two items below. Choose Allow in the macOS prompt. If the keychain prompt does not appear, use file storage.",
    "钥匙串（或改用文件保管）— 用来存 SSH / 云密钥": "Keychain (or file storage) — stores SSH and cloud secrets",
    "文件访问 — 允许读取本机 SSH 私钥（~/.ssh）": "Files — allow reading local SSH keys (~/.ssh)",
    "钥匙串": "Keychain",
    "待检查": "Not checked",
    "存放 SSH / 云密钥的主密钥。点「去授权」后在系统弹窗允许；弹不出则用「改用文件保管」。": "Master key for SSH and cloud secrets. Allow the system prompt, or use file storage.",
    "文件访问": "Files",
    "选取 ~/.ssh 下任意私钥文件，完成磁盘访问授权。": "Pick any private key under ~/.ssh to grant disk access.",
    "改用文件保管": "Use file storage",
    "重新检查": "Check again",
    "进入 Grip": "Enter Grip",
    "开通 SSH": "Enable SSH",
    "为云实例创建可执行 SSH 设备。WKWebView 不支持浏览器 prompt，请在此填写。": "Create an SSH device for this cloud instance.",
    "SSH 用户": "SSH user",
    "登录私钥": "Login key",
    "开通": "Enable",
    "锁": "Lock",
    "可达": "Reachable",
    "不可达": "Unreachable",
    "成功": "Succeeded",
    "失败": "Failed",
    "运行中": "Running",
    "运行": "Running",
    "排队": "Queued",
    "排队中": "Queued",
    "拒绝": "Denied",
    "未知": "Unknown",
    "待核对": "Needs check",
    "探测中": "Probing",
    "错误": "Error",
    "忙碌": "Busy",
    "已跳过": "Skipped",
    "云端运行中": "Running in cloud",
    "已关机": "Stopped",
    "关机中": "Stopping",
    "已终止": "Terminated",
    "终止中": "Terminating",
    "创建中": "Creating",
    "启动中": "Starting",
    "云状态未知": "Cloud state unknown",
    "探测资源": "Probe",
    "采样指标": "Sample metrics",
    "同步云": "Sync cloud",
    "同步全部": "Sync all",
    "探测": "Probe",
    "面板": "Panel",
    "执行中": "Running",
    "支持多通道": "Multi-path",
    "支持直连": "Direct path",
    "支持中继": "Relay path",
    "局域网之外还有汇合点通道。人在局域网里仍先走局域网 IP。": "A rendezvous path is also up. On the LAN, Grip still uses the LAN address first.",
    "汇合点直连还活着。人在局域网里仍先走局域网 IP。": "Rendezvous direct is up. On the LAN, Grip still uses the LAN address first.",
    "汇合点中继还活着。人在局域网里仍先走局域网 IP。": "Rendezvous relay is up. On the LAN, Grip still uses the LAN address first.",
    "直连": "Direct",
    "中继": "Relay",
    "中继(无直连权限)": "Relay (no direct permission)",
    "局域网": "LAN",
    "SSH连不上": "SSH failed",
    "SSH不可达": "SSH unreachable",
    "SSH可达": "SSH reachable",
    "未开通 SSH": "SSH not enabled",
    "缺凭证": "Missing secret",
    "已改走隧道": "Now using the tunnel",
    "以后再说": "Not now",
    "重试": "Retry",
    "重新接入": "Enroll again",
    "接口错误": "API error",
    "读不到设备列表": "Could not read devices",
    "该云暂不支持指标采样": "This cloud cannot sample metrics yet",
    "无「运行中」设备 — 再点筛选清除": "No running devices — click the filter again to clear",
    "无「可达」设备 — 再点筛选清除": "No reachable devices — click the filter again to clear",
    "无「不可达」设备 — 再点筛选清除": "No unreachable devices — click the filter again to clear",
    "打开面板": "Open panel",
    "固定浮窗": "Pin panel",
    "取消固定": "Unpin panel",
    "开启开机启动": "Turn on launch at login",
    "关闭开机启动": "Turn off launch at login",
    "退出 Grip": "Quit Grip",
    "筛选：运行": "Filter: running",
    "筛选：可达": "Filter: reachable",
    "筛选：不可达": "Filter: unreachable",
}

_MENU = {
    "zh": {
        "open": "打开面板",
        "pin": "固定浮窗",
        "unpin": "取消固定",
        "autostart_on": "开启开机启动",
        "autostart_off": "关闭开机启动",
        "quit": "退出 Grip",
        "lang_switch": "English",
    },
    "en": {
        "open": "Open panel",
        "pin": "Pin panel",
        "unpin": "Unpin panel",
        "autostart_on": "Turn on launch at login",
        "autostart_off": "Turn off launch at login",
        "quit": "Quit Grip",
        "lang_switch": "中文",
    },
}

_ROUTES = {
    "zh": {
        "lan": ("局域网", 15, "{when}走局域网直连。短命令通常几秒内返回。"),
        "tunnel": ("汇合点直连", 30, "{when}走汇合点直连。比局域网慢一些，短命令一般三十秒内返回。"),
        "relay": (
            "汇合点中继",
            90,
            "{when}走汇合点中继，往返明显更慢。短命令常常要十几秒到几十秒。"
            "请把等待放宽到至少 {expect_sec} 秒，不要在几秒内当成失败。",
        ),
        "down": ("不通", 0, "局域网、汇合点直连和汇合点中继现在都没有可用通道，命令没有发出去。"),
        "when_now": "本次",
        "when_current": "当前",
    },
    "en": {
        "lan": ("LAN", 15, "{when} uses the LAN address. A short command usually returns in a few seconds."),
        "tunnel": (
            "Rendezvous direct",
            30,
            "{when} uses rendezvous direct. Slower than LAN. A short command usually returns within 30 seconds.",
        ),
        "relay": (
            "Rendezvous relay",
            90,
            "{when} uses the rendezvous relay, which is much slower. A short command often takes tens of seconds. "
            "Wait at least {expect_sec} seconds before treating it as failed.",
        ),
        "down": (
            "Down",
            0,
            "LAN, rendezvous direct, and rendezvous relay are all unavailable. The command was not sent.",
        ),
        "when_now": "This call",
        "when_current": "This device",
    },
}


def _lang_path():
    return ensure_home() / "ui_lang"


def get_lang() -> str:
    try:
        raw = _lang_path().read_text(encoding="utf-8").strip()
    except OSError:
        return "zh"
    return raw if raw in _LANGS else "zh"


def set_lang(lang: str) -> str:
    picked = lang if lang in _LANGS else "zh"
    path = _lang_path()
    path.write_text(picked + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return picked


def menu(key: str) -> str:
    table = _MENU.get(get_lang()) or _MENU["zh"]
    return table.get(key) or _MENU["zh"][key]


def route_text(path: str, *, when: str = "本次") -> dict[str, Any]:
    lang = get_lang()
    pack = _ROUTES.get(lang) or _ROUTES["zh"]
    key = path if path in {"lan", "tunnel", "relay", "down"} else "down"
    if when == "当前":
        moment = pack["when_current"]
    else:
        moment = pack["when_now"]
    route, expect_sec, template = pack[key]
    return {
        "path": key,
        "route": route,
        "expect_sec": expect_sec,
        "note": template.format(when=moment, expect_sec=expect_sec),
    }
