from __future__ import annotations

"""Human primary entry: native tray / menu-bar app.

Delegates to ``grip.native_app`` (macOS NSStatusItem or Windows tray + panel).
"""


def run_menubar() -> None:
    from grip.native_app import run_native_menubar

    run_native_menubar()
