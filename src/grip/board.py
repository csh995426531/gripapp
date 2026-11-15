from __future__ import annotations

"""Back-compat: prefer ``grip start`` (native menu-bar)."""

import sys


def run_board(host: str = "127.0.0.1", port: int = 8765) -> None:
    if sys.platform == "darwin":
        from grip.native_app import run_native_menubar

        run_native_menubar(host=host, port=port)
        return
    from grip.web.app import run_ui

    run_ui(host=host, port=port, open_browser=False)
