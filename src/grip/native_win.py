from __future__ import annotations

"""Windows system-tray client (Mac menubar parity).

- Tray icon (pystray): left-click / 「打开面板」→ pywebview panel (same UI server)
- Context menu: open panel / pin / autostart / quit
- Background L2+L3 tick every 60s while tray is running
"""

import os
import sys
import threading
import traceback
from datetime import datetime
from typing import Any

from grip.native_pick import clear_open_path_chooser, register_open_path_chooser
from grip.native_save import clear_save_path_chooser, register_save_path_chooser
from grip.paths import ensure_home
from grip.web.app import start_ui_server


def _log(msg: str) -> None:
    base = os.environ.get("TEMP") or os.environ.get("TMP") or os.getcwd()
    path = os.path.join(base, "grip-menubar.log")
    line = f"[{datetime.now().strftime('%F %T')}] {msg}\n"
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except Exception:
        pass


def _tray_image():
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Industrial lime mark on dark — readable in light/dark taskbar themes.
    draw.rounded_rectangle(
        (4, 4, size - 5, size - 5), radius=12, fill=(18, 20, 15, 255)
    )
    draw.rounded_rectangle(
        (14, 18, size - 15, size - 14), radius=6, fill=(196, 240, 66, 255)
    )
    draw.rectangle((22, 10, size - 23, 18), fill=(196, 240, 66, 255))
    return img


def run_native_menubar(*, host: str = "127.0.0.1", port: int | None = None) -> None:
    if sys.platform != "win32":
        raise SystemExit("grip Windows tray requires win32")

    try:
        import pystray
        import webview
        from pystray import MenuItem as Item
    except ImportError as exc:
        raise SystemExit(
            "Install Windows UI deps: pip install 'grip[menubar]' "
            "(pystray, pywebview, pillow)"
        ) from exc

    from grip.win_dialogs import choose_open_path_win, choose_save_path_win

    ensure_home()
    httpd, url, _bind_port, svc = start_ui_server(host=host, port=port)
    print(
        f"Grip tray running · panel {url} (in-app, no browser)\n"
        "  Left-click icon  → open settings/board panel\n"
        "  Right-click icon → Open Panel / Pin / Autostart / Quit"
    )
    _log(f"starting Windows tray pid={os.getpid()} url={url}")

    register_open_path_chooser(
        lambda prompt, start_dir: choose_open_path_win(prompt, start_dir)
    )
    register_save_path_chooser(
        lambda default_name, prompt: choose_save_path_win(default_name, prompt)
    )

    state: dict[str, Any] = {
        "pinned": True,
        "metrics_busy": False,
        "icon": None,
        "stopping": False,
    }

    def _monitor_tick_bg() -> None:
        if state["stopping"]:
            return

        def work() -> None:
            try:
                from grip.monitor import probe_all

                probe_all(svc.resources)
                svc.refresh_status()
            except Exception:
                pass
            try:
                svc.reconcile_orphaned()
            except Exception:
                pass
            if state["metrics_busy"]:
                return
            state["metrics_busy"] = True
            try:
                try:
                    svc.sample_metrics()
                    svc.refresh_status()
                except Exception:
                    pass
            finally:
                state["metrics_busy"] = False

        threading.Thread(target=work, daemon=True).start()

    def _channel_tick_bg() -> None:
        if state["stopping"]:
            return

        def work() -> None:
            try:
                from grip.tunnel.channels import beat_channels

                beat_channels(svc.resources)
                svc.refresh_status()
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _schedule_channel_ticks() -> None:
        if state["stopping"]:
            return
        _channel_tick_bg()
        t = threading.Timer(20.0, _schedule_channel_ticks)
        t.daemon = True
        t.start()

    def _schedule_ticks() -> None:
        if state["stopping"]:
            return
        _monitor_tick_bg()
        _refresh_tooltip()
        t = threading.Timer(60.0, _schedule_ticks)
        t.daemon = True
        t.start()

    def _refresh_tooltip() -> None:
        icon = state.get("icon")
        if icon is None:
            return
        try:
            from grip.metrics import metrics_brief

            board = svc.board()
            n = int(board.get("running_count") or 0)
            h = board.get("resource_health") or {}
            down = int(h.get("unreachable") or 0)
            parts: list[str] = ["Grip"]
            if n or down:
                parts.append(f"运行 {n} · 不可达 {down}")
            briefs: list[str] = []
            for r in board.get("resources") or []:
                labels = r.get("labels") or {}
                m = labels.get("metrics") if isinstance(labels, dict) else None
                brief = metrics_brief(m if isinstance(m, dict) else None)
                if brief:
                    briefs.append(f"{r.get('name')}: {brief}")
                if len(briefs) >= 3:
                    break
            if briefs:
                parts.append(" · ".join(briefs))
            icon.title = " · ".join(parts) if len(parts) > 1 else "Grip"
        except Exception:
            pass

    def _show_panel() -> None:
        wins = list(webview.windows)
        if not wins:
            return
        w = wins[0]
        try:
            w.show()
        except Exception:
            pass
        try:
            w.restore()
        except Exception:
            pass

    def on_open(icon: Any = None, item: Any = None) -> None:  # noqa: ARG001
        _show_panel()

    def on_toggle_pin(icon: Any = None, item: Any = None) -> None:  # noqa: ARG001
        state["pinned"] = not bool(state["pinned"])
        _log(f"panel pinned={state['pinned']}")

    def open_label(item: Any) -> str:  # noqa: ARG001
        from grip.i18n import menu

        return menu("open")

    def quit_label(item: Any) -> str:  # noqa: ARG001
        from grip.i18n import menu

        return menu("quit")

    def pin_label(item: Any) -> str:  # noqa: ARG001
        from grip.i18n import menu

        return menu("unpin") if state["pinned"] else menu("pin")

    def autostart_label(item: Any) -> str:  # noqa: ARG001
        from grip.i18n import menu

        try:
            from grip import autostart

            enabled = autostart.is_enabled()
        except Exception:
            enabled = False
        return menu("autostart_off" if enabled else "autostart_on")

    def lang_label(item: Any) -> str:  # noqa: ARG001
        from grip.i18n import menu

        return menu("lang_switch")

    def on_lang(icon: Any = None, item: Any = None) -> None:  # noqa: ARG001
        from grip.i18n import get_lang, set_lang

        nxt = "en" if get_lang() != "en" else "zh"
        set_lang(nxt)
        try:
            wins = list(webview.windows)
            if wins:
                wins[0].evaluate_js(f"setLang('{nxt}')")
        except Exception as exc:
            _log(f"lang switch failed: {exc}")

    def on_autostart(icon: Any = None, item: Any = None) -> None:  # noqa: ARG001
        try:
            from grip import autostart

            if autostart.is_enabled():
                autostart.disable()
                _log("autostart disabled")
            else:
                autostart.enable()
                _log("autostart enabled")
        except Exception as exc:
            _log(f"autostart toggle failed: {exc}")

    def on_quit(icon: Any = None, item: Any = None) -> None:  # noqa: ARG001
        state["stopping"] = True
        clear_save_path_chooser()
        clear_open_path_chooser()
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            for w in list(webview.windows):
                w.destroy()
        except Exception:
            pass
        try:
            if icon is not None:
                icon.stop()
        except Exception:
            pass
        # webview.start() returns after all windows destroyed.

    menu = pystray.Menu(
        Item(open_label, on_open, default=True),
        Item(pin_label, on_toggle_pin),
        pystray.Menu.SEPARATOR,
        Item(autostart_label, on_autostart),
        Item(lang_label, on_lang),
        pystray.Menu.SEPARATOR,
        Item(quit_label, on_quit),
    )
    icon = pystray.Icon("Grip", _tray_image(), "Grip", menu)
    state["icon"] = icon

    def tray_thread() -> None:
        try:
            icon.run()
        except Exception:
            _log("tray failed:\n" + traceback.format_exc())

    threading.Thread(target=tray_thread, daemon=True, name="grip-tray").start()
    _schedule_ticks()
    _schedule_channel_ticks()

    window = webview.create_window(
        "Grip",
        url,
        width=440,
        height=720,
        hidden=True,
        text_select=True,
    )

    def _on_closing() -> bool:
        # Pinned: hide instead of quit (Mac popover pin parity).
        if state["pinned"] and not state["stopping"]:
            try:
                window.hide()
            except Exception:
                pass
            return False
        return True

    try:
        window.events.closing += _on_closing
    except Exception:
        pass

    _log("status item ready (Windows tray)")
    try:
        webview.start()
    finally:
        state["stopping"] = True
        clear_save_path_chooser()
        clear_open_path_chooser()
        try:
            icon.stop()
        except Exception:
            pass
        try:
            httpd.shutdown()
        except Exception:
            pass
