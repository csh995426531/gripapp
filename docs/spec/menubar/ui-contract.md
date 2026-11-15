# Menubar UI Contract (Code-Spec)

## 1. Scope / Trigger

**Primary human UX** on macOS (NSStatusItem) and Windows (system tray). Not browser-primary.

## 2. Signatures

* Process: `grip start` / `grip menubar` (also `grip ui` / `grip board` on Darwin/Win32)
* Implementation: `grip.native_app.run_native_menubar`
  * macOS → AppKit + WKWebView popover
  * Windows → `grip.native_win` (pystray + pywebview)
* Panel content: local ThreadingHTTPServer + embedded web panel
* Data: live `/api/*` against `GripService`; status snapshot also at `~/.grip/status.json`

## 3. Contracts

| Interaction | Behavior |
|-------------|----------|
| Left-click tray / status item | Open in-app panel (settings + board) |
| Right-click | Native menu: 打开面板 / 探测 / 指标 / 同步 / 开机启动 / 退出 |
| Background tick | L2 probe + L3 metrics ~60s while tray running |
| Secrets | Never render secret fields; API payloads redacted |

## 4. Validation & Error Matrix

| Case | Behavior |
|------|----------|
| Missing UI deps | Exit with `pip install 'grip[menubar]'` |
| Port 8765 busy | Auto-pick free port |
| Other OS | `start` falls back to local UI server only (no browser open) |

## 5. Good / Base / Bad

* Good: left-click opens panel without launching a system browser; right-click shows Quit.
* Base: empty resources/tasks in panel.
* Bad: primary flow calls `webbrowser.open` / Chrome/Edge/Safari.

## 6. Tests Required

* `test_ui_server_no_browser` (unit: server starts, serves HTML, no webbrowser)
* `test_refresh_win_launcher_writes_cmd` (Windows launcher script)
* Manual: `grip start` — verify left/right click on macOS and Windows

## 7. Wrong vs Correct

* **Wrong**: tray opens external browser as main UI; menu-only without panel.
* **Correct**: native tray/status item + embedded panel; AI remains MCP.
