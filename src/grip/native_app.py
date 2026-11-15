from __future__ import annotations

"""Native tray / menu-bar entry (macOS status item or Windows tray).

macOS (this module):
- Lives in the menu bar as NSStatusItem (no Dock icon)
- Left-click → floating NSPanel (top-right docked) with WKWebView
- Right-click / Control-click → native NSMenu
- Background L2+L3 tick while running

Windows: delegates to ``grip.native_win`` (pystray + pywebview).
Never opens a system browser as the primary UI.
"""

import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from grip.native_pick import clear_open_path_chooser, register_open_path_chooser
from grip.native_save import ChooseStatus, clear_save_path_chooser, register_save_path_chooser
from grip.paths import ensure_home
from grip.web.app import start_ui_server


def _menubar_log(msg: str) -> None:
    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), "grip-menubar.log")
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


def run_native_menubar(*, host: str = "127.0.0.1", port: int | None = None) -> None:
    if sys.platform == "win32":
        from grip.native_win import run_native_menubar as _run_win

        _run_win(host=host, port=port)
        return
    if sys.platform != "darwin":
        raise SystemExit("grip start / menubar requires macOS or Windows")

    _menubar_log("darwin: importing AppKit/WebKit")
    try:
        from AppKit import (  # type: ignore
            NSApp,
            NSApplication,
            NSApplicationActivationPolicyAccessory,
            NSBackingStoreBuffered,
            NSEvent,
            NSEventMaskRightMouseUp,
            NSFloatingWindowLevel,
            NSImage,
            NSImageSymbolConfiguration,
            NSMakeRect,
            NSMenu,
            NSMenuItem,
            NSObject,
            NSPanel,
            NSScreen,
            NSStatusBar,
            NSViewController,
        )
        try:
            from AppKit import NSWindowStyleMaskBorderless as _BORDERLESS  # type: ignore
        except ImportError:
            from AppKit import NSBorderlessWindowMask as _BORDERLESS  # type: ignore
        try:
            from AppKit import NSWindowStyleMaskResizable as _RESIZABLE  # type: ignore
        except ImportError:
            from AppKit import NSResizableWindowMask as _RESIZABLE  # type: ignore
        try:
            from AppKit import (  # type: ignore
                NSCursor,
                NSView,
                NSViewHeightSizable,
                NSViewWidthSizable,
            )
        except ImportError:  # pragma: no cover
            NSCursor = None  # type: ignore
            NSView = NSObject  # type: ignore
            NSViewWidthSizable = 2
            NSViewHeightSizable = 16
        from Foundation import NSTimer, NSURL, NSURLRequest, NSMakeSize  # type: ignore
        from WebKit import WKWebView, WKWebViewConfiguration  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Install macOS UI deps: pip install 'grip[menubar]' "
            "(pyobjc-framework-Cocoa, pyobjc-framework-WebKit)"
        ) from exc

    _menubar_log("darwin: AppKit ok; ensure_home")
    ensure_home()
    _menubar_log("darwin: ensure_home done; starting UI server")
    httpd, url, _bind_port, svc = start_ui_server(host=host, port=port)
    _menubar_log(f"darwin: UI server ready {url}")
    try:
        from grip.panel_proxy import write_panel_meta

        write_panel_meta(url=url, pid=os.getpid())
        _menubar_log(f"wrote panel.json for MCP proxy url={url}")
    except Exception as exc:  # noqa: BLE001
        _menubar_log(f"panel.json write skipped: {exc}")
    print(
        f"Grip menu-bar running · panel {url} (in-app, no browser)\n"
        "  Left-click icon  → open settings/board panel\n"
        "  Right-click icon → Open Panel / Pin / Autostart / Quit"
    )

    class GripPanel(NSPanel):
        """Borderless panels refuse key status by default — override so WKWebView inputs work."""

        def canBecomeKeyWindow(self) -> bool:  # noqa: N802
            return True

        def canBecomeMainWindow(self) -> bool:  # noqa: N802
            return True

    class BottomResizeStrip(NSView):
        """Drag handle under the webview — grow/shrink panel height, keep top edge fixed."""

        _grip_h = 14.0
        _drag_origin_y = None
        _drag_frame = None
        _min_h = 480.0
        _max_h = 1400.0

        def resetCursorRects(self) -> None:  # noqa: N802
            try:
                if NSCursor is not None:
                    self.addCursorRect_cursor_(
                        self.bounds(), NSCursor.resizeUpDownCursor()
                    )
            except Exception:
                pass

        def drawRect_(self, _rect) -> None:  # noqa: N802
            try:
                from AppKit import NSBezierPath, NSColor  # type: ignore

                NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.08, 0.09, 0.06, 0.92
                ).setFill()
                NSBezierPath.fillRect_(self.bounds())
                # Centered pill affordance
                b = self.bounds()
                pill_w, pill_h = 36.0, 3.0
                px = (float(b.size.width) - pill_w) / 2.0
                py = (float(b.size.height) - pill_h) / 2.0
                NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.60, 0.64, 0.54, 0.45
                ).setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(px, py, pill_w, pill_h), 1.5, 1.5
                ).fill()
            except Exception:
                pass

        def mouseDown_(self, event) -> None:  # noqa: N802
            try:
                self._drag_origin_y = float(NSEvent.mouseLocation().y)
                self._drag_frame = self.window().frame()
            except Exception:
                self._drag_origin_y = None
                self._drag_frame = None

        def mouseDragged_(self, event) -> None:  # noqa: N802
            if self._drag_origin_y is None or self._drag_frame is None:
                return
            win = self.window()
            if win is None:
                return
            try:
                now_y = float(NSEvent.mouseLocation().y)
                # Screen Y grows upward; dragging the bottom down increases height.
                delta = self._drag_origin_y - now_y
                old = self._drag_frame
                new_h = float(old.size.height) + delta
                new_h = max(float(self._min_h), min(float(self._max_h), new_h))
                top = float(old.origin.y) + float(old.size.height)
                new_y = top - new_h
                win.setFrame_display_(
                    NSMakeRect(
                        float(old.origin.x), new_y, float(old.size.width), new_h
                    ),
                    True,
                )
            except Exception:
                pass

        def mouseUp_(self, _event) -> None:  # noqa: N802
            self._drag_origin_y = None
            self._drag_frame = None
            try:
                win = self.window()
                delg = win.delegate() if win is not None else None
                if delg is not None and hasattr(delg, "_remember_panel_size"):
                    delg._remember_panel_size()
            except Exception:
                pass

    class _MainThreadRunner(NSObject):
        """Run a callable on the AppKit main thread (for NSSavePanel)."""

        _fn = None
        _box: dict[str, Any] | None = None

        def run_(self, _obj) -> None:  # noqa: N802
            box = self._box if self._box is not None else {}
            fn = self._fn
            try:
                box["result"] = fn() if fn is not None else None
            except Exception as exc:  # noqa: BLE001
                box["exc"] = exc

    _main_runner = _MainThreadRunner.alloc().init()

    def _run_on_main(fn: Any) -> Any:
        from Foundation import NSThread  # type: ignore

        if NSThread.isMainThread():
            return fn()
        box: dict[str, Any] = {}
        _main_runner._fn = fn
        _main_runner._box = box
        _main_runner.performSelectorOnMainThread_withObject_waitUntilDone_(
            "run:", None, True
        )
        if "exc" in box:
            raise box["exc"]
        return box.get("result")

    from grip.native_main import clear_main_runner, register_main_runner

    register_main_runner(_run_on_main)

    def _ns_save_chooser(
        default_name: str, prompt: str
    ) -> tuple[str | None, ChooseStatus]:
        def show() -> tuple[str | None, ChooseStatus]:
            from AppKit import NSApp, NSOKButton, NSSavePanel  # type: ignore

            try:
                NSApp.activateIgnoringOtherApps_(True)
            except Exception:
                pass
            panel = NSSavePanel.savePanel()
            panel.setCanCreateDirectories_(True)
            panel.setExtensionHidden_(False)
            panel.setNameFieldStringValue_(default_name)
            panel.setTitle_("导出日志")
            panel.setMessage_(prompt or "选择保存位置")
            try:
                panel.setPrompt_("保存")
            except Exception:
                pass
            if panel.runModal() != NSOKButton:
                return None, "cancelled"
            url_obj = panel.URL()
            if url_obj is None:
                return None, "cancelled"
            path = url_obj.path()
            if not path:
                return None, "cancelled"
            return str(path), "ok"

        try:
            return _run_on_main(show)
        except Exception as exc:
            _menubar_log(f"NSSavePanel failed: {exc}")
            return None, "unavailable"

    register_save_path_chooser(_ns_save_chooser)

    def _ns_open_chooser(
        prompt: str, start_dir: str | None = None
    ) -> tuple[str | None, ChooseStatus]:
        def show() -> tuple[str | None, ChooseStatus]:
            from AppKit import NSApp, NSOKButton, NSOpenPanel  # type: ignore
            from Foundation import NSURL  # type: ignore

            from grip.native_pick import resolve_start_dir

            try:
                NSApp.activateIgnoringOtherApps_(True)
            except Exception:
                pass
            panel = NSOpenPanel.openPanel()
            panel.setCanChooseFiles_(True)
            panel.setCanChooseDirectories_(False)
            panel.setAllowsMultipleSelection_(False)
            panel.setTitle_("选择文件")
            panel.setMessage_(prompt or "选择文件")
            try:
                panel.setPrompt_("选择")
            except Exception:
                pass
            initial = resolve_start_dir(start_dir)
            if initial:
                try:
                    panel.setDirectoryURL_(NSURL.fileURLWithPath_(initial))
                except Exception:
                    pass
            if panel.runModal() != NSOKButton:
                return None, "cancelled"
            url_obj = panel.URL()
            if url_obj is None:
                return None, "cancelled"
            path = url_obj.path()
            if not path:
                return None, "cancelled"
            return str(path), "ok"

        try:
            return _run_on_main(show)
        except Exception as exc:
            _menubar_log(f"NSOpenPanel failed: {exc}")
            return None, "unavailable"

    register_open_path_chooser(_ns_open_chooser)

    class AppDelegate(NSObject):
        statusItem = None
        panel = None  # floating NSPanel, always docked top-right
        webView = None
        rootView = None
        resizeStrip = None
        contextMenu = None
        _svc = None
        _url = None
        _eventMonitor = None
        _panel_loaded = False
        _pinned = True  # stay open while switching apps to paste configs
        _panel_w = 440.0
        _panel_h = 760.0
        _panel_margin = 10.0
        _panel_min_w = 380.0
        _panel_min_h = 480.0
        _panel_max_w = 720.0
        _panel_max_h = 1400.0
        _grip_h = 14.0
        _pinning = False

        def applicationDidFinishLaunching_(self, _notification) -> None:  # noqa: N802
            try:
                self._svc = svc
                self._url = url
                _menubar_log("applicationDidFinishLaunching")

                bar = NSStatusBar.systemStatusBar()
                self.statusItem = bar.statusItemWithLength_(22.0)
                try:
                    self.statusItem.setVisible_(True)
                except Exception:
                    pass
                button = self.statusItem.button()
                if button is None:
                    raise RuntimeError("NSStatusItem button unavailable")

                self._set_icon(button)
                button.setToolTip_("Grip")
                try:
                    button.setImagePosition_(2)  # NSImageLeft
                except Exception:
                    pass
                button.setTarget_(self)
                button.setAction_("leftClick:")
                # Left mouse only for the button action; right-click via event monitor
                try:
                    from AppKit import NSEventMaskLeftMouseUp

                    button.sendActionOn_(NSEventMaskLeftMouseUp)
                except Exception:
                    pass

                self.contextMenu = self._build_menu()
                # Do NOT call statusItem.setMenu_ — that would steal left-clicks
                # Hidden Edit menu so Cmd+C / Cmd+V reach WKWebView inputs.
                self._install_edit_menu()

                config = WKWebViewConfiguration.alloc().init()
                try:
                    # Allow clipboard read/write from the panel (paste configs).
                    config.preferences().setValue_forKey_(
                        True, "allowFileAccessFromFileURLs"
                    )
                except Exception:
                    pass
                frame = NSMakeRect(0, 0, self._panel_w, self._panel_h)
                grip_h = float(self._grip_h)
                self.rootView = NSView.alloc().initWithFrame_(frame)
                self.webView = WKWebView.alloc().initWithFrame_configuration_(
                    NSMakeRect(0, grip_h, self._panel_w, self._panel_h - grip_h),
                    config,
                )
                self.resizeStrip = BottomResizeStrip.alloc().initWithFrame_(
                    NSMakeRect(0, 0, self._panel_w, grip_h)
                )
                self.resizeStrip._min_h = float(self._panel_min_h)
                self.resizeStrip._max_h = float(self._panel_max_h)
                self.resizeStrip._grip_h = grip_h
                # Borderless floating panel — no macOS traffic-light buttons.
                # Docked top-right; toggle via status item (not a normal window).
                # Must use GripPanel: stock borderless NSPanel cannot become key,
                # so WKWebView text fields would ignore typing.
                # Resizable + bottom drag strip to grow/shrink height.
                self.panel = GripPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                    frame, int(_BORDERLESS) | int(_RESIZABLE), NSBackingStoreBuffered, False
                )
                self.panel.setTitle_("Grip")
                self.panel.setFloatingPanel_(True)
                self.panel.setBecomesKeyOnlyIfNeeded_(False)
                self.panel.setLevel_(NSFloatingWindowLevel)
                self.panel.setReleasedWhenClosed_(False)
                try:
                    self.panel.setMinSize_(
                        NSMakeSize(self._panel_min_w, self._panel_min_h)
                    )
                    self.panel.setMaxSize_(
                        NSMakeSize(self._panel_max_w, self._panel_max_h)
                    )
                except Exception:
                    pass
                try:
                    self.panel.setOpaque_(False)
                    self.panel.setBackgroundColor_(
                        __import__("AppKit", fromlist=["NSColor"]).NSColor.clearColor()
                    )
                except Exception:
                    pass
                try:
                    self.panel.setHasShadow_(True)
                    self.panel.setMovableByWindowBackground_(False)
                except Exception:
                    pass
                self.rootView.addSubview_(self.webView)
                self.rootView.addSubview_(self.resizeStrip)
                try:
                    self.webView.setAutoresizingMask_(
                        int(NSViewWidthSizable) | int(NSViewHeightSizable)
                    )
                    self.resizeStrip.setAutoresizingMask_(int(NSViewWidthSizable))
                    self.rootView.setAutoresizingMask_(
                        int(NSViewWidthSizable) | int(NSViewHeightSizable)
                    )
                except Exception:
                    pass
                self.panel.setContentView_(self.rootView)
                try:
                    self.webView.setValue_forKey_(False, "drawsBackground")
                except Exception:
                    pass
                try:
                    self.panel.setDelegate_(self)
                except Exception:
                    pass
                self._pinned = True
                self._apply_pin_behavior()
                self._reposition_panel()

                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    5.0, self, "refreshTitle:", None, True
                )
                # L2 probe + L3 metrics while popover closed (panel JS only when open)
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    60.0, self, "tickMetrics:", None, True
                )
                # LAN 20s, direct 60s, relay 180s. The beater itself decides which are due.
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    20.0, self, "tickChannels:", None, True
                )
                self.refreshTitle_(None)
                self.tickChannels_(None)
                self.tickMetrics_(None)

                def right_click_handler(event: Any) -> Any:
                    btn = self.statusItem.button() if self.statusItem else None
                    if btn is None:
                        return event
                    win = event.window()
                    if win is not None and win == btn.window():
                        self.showContextMenu_(btn)
                        return None
                    return event

                self._eventMonitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                    NSEventMaskRightMouseUp, right_click_handler
                )
                _menubar_log(
                    f"status item ready title={button.title()!r} url={url}"
                )
                # Open panel once so the app is findable even if a menu-bar
                # manager (Ice/Bartender) hides the status item.
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    0.4, self, "openPanelOnLaunch:", None, False
                )
            except Exception:
                _menubar_log("status item setup FAILED:\n" + traceback.format_exc())
                raise

        def openPanelOnLaunch_(self, _timer) -> None:  # noqa: N802
            try:
                self.openPanel_(None)
                _menubar_log("auto-opened panel on launch (top-right)")
            except Exception as exc:
                _menubar_log(f"auto-open panel failed: {exc}")

        def _screen_for_panel(self) -> Any:
            """Prefer the screen that holds the status item; else main screen."""
            try:
                btn = self.statusItem.button() if self.statusItem else None
                if btn is not None and btn.window() is not None:
                    scr = btn.window().screen()
                    if scr is not None:
                        return scr
            except Exception:
                pass
            return NSScreen.mainScreen()

        def _remember_panel_size(self) -> None:
            if self.panel is None:
                return
            try:
                size = self.panel.frame().size
                self._panel_w = float(size.width)
                self._panel_h = float(size.height)
            except Exception:
                pass

        def _reposition_panel(self) -> None:
            """Pin the floating panel to the top-right of the target screen."""
            if self.panel is None or self._pinning:
                return
            screen = self._screen_for_panel()
            if screen is None:
                return
            # Prefer live size (user may have dragged the bottom edge).
            try:
                cur = self.panel.frame().size
                if float(cur.width) >= 200 and float(cur.height) >= 200:
                    self._panel_w = float(cur.width)
                    self._panel_h = float(cur.height)
            except Exception:
                pass
            # visibleFrame excludes menu bar + Dock — dock under the menu bar.
            vis = screen.visibleFrame()
            full = screen.frame()
            margin = float(self._panel_margin)
            width = float(self._panel_w)
            height = min(float(self._panel_h), float(vis.size.height) - margin)
            # Always flush to the physical right edge of this screen.
            x = float(full.origin.x) + float(full.size.width) - width - margin
            # Top of the usable area (just below the menu bar).
            top = float(vis.origin.y) + float(vis.size.height)
            y = top - height - 2.0
            if y < float(vis.origin.y):
                y = float(vis.origin.y)
            self._pinning = True
            try:
                self.panel.setFrame_display_(
                    NSMakeRect(x, y, width, height), True
                )
                grip_h = float(self._grip_h)
                if self.rootView is not None:
                    self.rootView.setFrame_(NSMakeRect(0, 0, width, height))
                if self.webView is not None:
                    self.webView.setFrame_(
                        NSMakeRect(0, grip_h, width, max(1.0, height - grip_h))
                    )
                if self.resizeStrip is not None:
                    self.resizeStrip.setFrame_(NSMakeRect(0, 0, width, grip_h))
            finally:
                self._pinning = False

        def windowDidResize_(self, _notification) -> None:  # noqa: N802
            if self.panel is None or self._pinning:
                return
            self._remember_panel_size()

        def windowDidEndLiveResize_(self, _notification) -> None:  # noqa: N802
            """After bottom/side drag, re-pin top-right with the new size."""
            if self.panel is None or self._pinning:
                return
            self._remember_panel_size()
            self._reposition_panel()

        def _menubar_icon_path(self) -> str | None:
            """Resolve packaged menubar template PNG (hex node mark)."""
            candidates: list[Path] = []
            # Frozen app: Resources next to MacOS, or _MEIPASS
            if getattr(sys, "frozen", False):
                exe = Path(sys.executable).resolve()
                candidates.append(
                    exe.parent.parent / "Resources" / "grip_icons" / "aaron.s@example.org"
                )
                candidates.append(
                    exe.parent.parent / "Resources" / "grip_icons" / "menubar.png"
                )
                meipass = getattr(sys, "_MEIPASS", None)
                if meipass:
                    candidates.append(Path(meipass) / "grip_icons" / "aaron.s@example.org")
                    candidates.append(Path(meipass) / "grip_icons" / "menubar.png")
            # Dev tree
            here = Path(__file__).resolve().parent
            root = here.parents[1]  # repo root when src/grip/...
            candidates.append(root / "packaging" / "icons" / "aaron.s@example.org")
            candidates.append(root / "packaging" / "icons" / "menubar.png")
            for p in candidates:
                if p.is_file():
                    return str(p)
            return None

        def _set_icon(self, button: Any) -> None:
            # Icon only — no "Grip" title text in the menu bar.
            button.setTitle_("")
            img = None
            path = self._menubar_icon_path()
            try:
                if path:
                    img = NSImage.alloc().initWithContentsOfFile_(path)
                    if img is not None:
                        img.setTemplate_(True)
                        try:
                            img.setSize_((18.0, 18.0))
                        except Exception:
                            pass
                if img is None:
                    conf = NSImageSymbolConfiguration.configurationWithPointSize_weight_scale_(
                        14, 0, 1
                    )
                    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                        "hand.raised.fill", "Grip"
                    )
                    if img is not None and conf is not None:
                        img = img.imageWithSymbolConfiguration_(conf)
                    if img is not None:
                        img.setTemplate_(True)
                if img is not None:
                    button.setImage_(img)
                    try:
                        button.setImagePosition_(1)  # NSImageOnly
                    except Exception:
                        pass
            except Exception:
                button.setImage_(None)
            button.setToolTip_("Grip")

        def _autostart_title(self) -> str:
            from grip.i18n import menu

            try:
                from grip import autostart

                enabled = autostart.is_enabled()
            except Exception:
                enabled = False
            return menu("autostart_off" if enabled else "autostart_on")

        def _build_menu(self) -> Any:
            from grip.i18n import menu

            menu_obj = NSMenu.alloc().init()
            specs = [
                (menu("open"), "openPanel:"),
                (menu("unpin") if self._pinned else menu("pin"), "togglePin:"),
                None,
                (self._autostart_title(), "toggleAutostart:"),
                (menu("lang_switch"), "toggleLang:"),
                None,
                (menu("quit"), "quitApp:"),
            ]
            for spec in specs:
                if spec is None:
                    menu_obj.addItem_(NSMenuItem.separatorItem())
                    continue
                title, action = spec
                item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    title, action, ""
                )
                item.setTarget_(self)
                menu_obj.addItem_(item)
            return menu_obj

        def _install_edit_menu(self) -> None:
            """Accessory apps need an Edit menu for Cmd+V / Cmd+C to work in WKWebView."""
            try:
                main = NSApp.mainMenu()
                if main is None:
                    main = NSMenu.alloc().init()
                    NSApp.setMainMenu_(main)
                # Avoid duplicating if relaunched in-process
                for i in range(main.numberOfItems()):
                    it = main.itemAtIndex_(i)
                    if it is not None and it.title() in ("Edit", "编辑"):
                        return
                edit = NSMenu.alloc().initWithTitle_("Edit")
                edit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    "Edit", None, ""
                )
                edit_item.setSubmenu_(edit)
                try:
                    from AppKit import NSEventModifierFlagCommand  # type: ignore

                    cmd_mask = NSEventModifierFlagCommand
                except Exception:
                    cmd_mask = 1 << 20  # NSEventModifierFlagCommand
                for title, action, key in (
                    ("Cut", "cut:", "x"),
                    ("Copy", "copy:", "c"),
                    ("Paste", "paste:", "v"),
                    ("Select All", "selectAll:", "a"),
                ):
                    mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                        title, action, key
                    )
                    # Explicit ⌘ so bare letter keys are never stolen from inputs.
                    try:
                        mi.setKeyEquivalentModifierMask_(cmd_mask)
                    except Exception:
                        pass
                    edit.addItem_(mi)
                main.addItem_(edit_item)
            except Exception as exc:
                _menubar_log(f"edit menu install failed: {exc}")

        def _apply_pin_behavior(self) -> None:
            if self.panel is None:
                return
            # Pinned: stay visible when focus leaves (paste configs into forms).
            self.panel.setHidesOnDeactivate_(not bool(self._pinned))

        def _panel_visible(self) -> bool:
            return bool(self.panel is not None and self.panel.isVisible())

        def _hide_panel(self) -> None:
            if self.panel is not None:
                self.panel.orderOut_(None)

        def togglePin_(self, _sender) -> None:  # noqa: N802
            self._pinned = not bool(self._pinned)
            self._apply_pin_behavior()
            self.contextMenu = self._build_menu()
            _menubar_log(f"panel pinned={self._pinned}")

        def leftClick_(self, sender) -> None:  # noqa: N802
            # Control-click → context menu (same as right-click)
            event = NSApp.currentEvent()
            if event is not None and (event.modifierFlags() & (1 << 18)):  # NSEventModifierFlagControl
                self.showContextMenu_(sender)
                return
            self.togglePopover_(sender)

        def showContextMenu_(self, sender) -> None:  # noqa: N802
            if self._panel_visible():
                self._hide_panel()
            self.contextMenu = self._build_menu()
            self.statusItem.popUpStatusItemMenu_(self.contextMenu)

        def togglePopover_(self, sender) -> None:  # noqa: N802
            if self.panel is None:
                return
            if self._panel_visible():
                self._hide_panel()
            else:
                self.openPanel_(sender)

        def openPanel_(self, sender) -> None:  # noqa: N802
            if self.panel is None or self.webView is None:
                return
            try:
                NSApp.activateIgnoringOtherApps_(True)
            except Exception:
                pass
            self._apply_pin_behavior()
            self._reposition_panel()
            # Load once — reloading wiped form state (add device / cloud credentials).
            if not self._panel_loaded:
                base = NSURL.URLWithString_(self._url)
                try:
                    import urllib.request

                    html = urllib.request.urlopen(self._url, timeout=2).read().decode(
                        "utf-8", errors="replace"
                    )
                    self.webView.loadHTMLString_baseURL_(html, base)
                    self._panel_loaded = True
                except Exception as exc:
                    _menubar_log(f"loadHTMLString failed, fallback loadRequest: {exc}")
                    req = NSURLRequest.requestWithURL_(base)
                    self.webView.loadRequest_(req)
                    self._panel_loaded = True
            self.panel.makeKeyAndOrderFront_(None)
            # Focus webview so paste / typing works immediately
            try:
                self.panel.makeFirstResponder_(self.webView)
            except Exception:
                pass

        def _monitor_tick_bg(self) -> None:
            """Cheap L2 TCP probe always; L3 sample when free.

            Probe updates reachable/unreachable within ~tcp timeout without
            waiting on SSH metrics. Uses probe_all directly (no task row spam).
            """
            if self._svc is None:
                return

            def work() -> None:
                try:
                    from grip.monitor import probe_all

                    probe_all(self._svc.resources)
                    self._svc.refresh_status()
                except Exception:
                    pass
                try:
                    self._svc.reconcile_orphaned()
                except Exception:
                    pass
                if getattr(self, "_metrics_busy", False):
                    return
                self._metrics_busy = True
                try:
                    try:
                        self._svc.sample_metrics()
                        self._svc.refresh_status()
                    except Exception:
                        pass
                finally:
                    self._metrics_busy = False

            threading.Thread(target=work, daemon=True).start()

        def tickChannels_(self, _timer) -> None:  # noqa: N802
            if self._svc is None:
                return

            def work() -> None:
                try:
                    from grip.tunnel.channels import beat_channels

                    beat_channels(self._svc.resources)
                    self._svc.refresh_status()
                except Exception:
                    pass

            threading.Thread(target=work, daemon=True).start()

        def tickMetrics_(self, _timer) -> None:  # noqa: N802
            """Background L2+L3 tick (popover may be closed; panel JS is not enough)."""
            self._monitor_tick_bg()

        def refreshTitle_(self, _timer) -> None:  # noqa: N802
            button = self.statusItem.button() if self.statusItem else None
            if button is None or self._svc is None:
                return
            try:
                from grip.metrics import metrics_brief

                board = self._svc.board()
                n = int(board.get("running_count") or 0)
                h = board.get("resource_health") or {}
                down = int(h.get("unreachable") or 0)
                parts: list[str] = ["Grip"]
                if n or down:
                    parts.append(f"运行 {n} · 不可达 {down}")
                # Surface freshest L3 sample in tooltip when popover is closed
                # (board summaries expose resource.meta as labels)
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
                button.setToolTip_(" · ".join(parts) if len(parts) > 1 else "Grip")
            except Exception:
                pass

        def toggleLang_(self, _sender) -> None:  # noqa: N802
            from grip.i18n import get_lang, set_lang

            nxt = "en" if get_lang() != "en" else "zh"
            set_lang(nxt)
            if self.webView is None or not self._panel_loaded:
                return
            try:
                self.webView.evaluateJavaScript_completionHandler_(
                    f"setLang('{nxt}')", None
                )
            except Exception as exc:
                _menubar_log(f"lang switch failed: {exc}")

        def toggleAutostart_(self, _sender) -> None:  # noqa: N802
            try:
                from grip import autostart

                if autostart.is_enabled():
                    autostart.disable()
                    _menubar_log("autostart disabled")
                else:
                    autostart.enable()
                    _menubar_log("autostart enabled")
            except Exception as exc:
                _menubar_log(f"autostart toggle failed: {exc}")

        def quitApp_(self, _sender) -> None:  # noqa: N802
            clear_save_path_chooser()
            clear_open_path_chooser()
            clear_main_runner()
            try:
                from grip.panel_proxy import clear_panel_meta

                clear_panel_meta()
            except Exception:
                pass
            try:
                httpd.shutdown()
            except Exception:
                pass
            NSApp.terminate_(None)

    _menubar_log(f"starting NSApplication pid={os.getpid()}")
    app = NSApplication.sharedApplication()
    # Regular (Dock icon) so the app stays findable when Ice/Bartender hides
    # the status item. LSUIElement in Info.plist still applies to packaged
    # launches unless we override here — Regular wins at runtime.
    try:
        from AppKit import NSApplicationActivationPolicyRegular

        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    except Exception:
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    # Ensure launch notification fires even when not opened via LaunchServices
    try:
        app.finishLaunching()
    except Exception:
        pass
    try:
        app.run()
    finally:
        clear_save_path_chooser()
        clear_open_path_chooser()
        clear_main_runner()
        try:
            from grip.panel_proxy import clear_panel_meta

            clear_panel_meta()
        except Exception:
            pass

