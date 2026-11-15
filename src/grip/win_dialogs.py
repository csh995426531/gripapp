"""Windows native open/save file dialogs via Win32 (any thread)."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from grip.native_pick import resolve_start_dir
from grip.native_save import ChooseStatus

OFN_FILEMUSTEXIST = 0x00001000
OFN_PATHMUSTEXIST = 0x00000800
OFN_OVERWRITEPROMPT = 0x00000002
OFN_EXPLORER = 0x00080000
OFN_HIDEREADONLY = 0x00000004
OFN_NOCHANGEDIR = 0x00000008


class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", wintypes.LPVOID),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", wintypes.LPVOID),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def _comdlg() -> ctypes.WinDLL:
    return ctypes.WinDLL("comdlg32", use_last_error=True)


def choose_open_path_win(
    prompt: str = "选择文件", start_dir: str | None = None
) -> tuple[str | None, ChooseStatus]:
    buf = ctypes.create_unicode_buffer(32768)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile = len(buf)
    ofn.lpstrTitle = prompt or "选择文件"
    ofn.lpstrFilter = "All files (*.*)\0*.*\0\0"
    ofn.nFilterIndex = 1
    ofn.Flags = (
        OFN_EXPLORER
        | OFN_FILEMUSTEXIST
        | OFN_PATHMUSTEXIST
        | OFN_HIDEREADONLY
        | OFN_NOCHANGEDIR
    )
    initial = resolve_start_dir(start_dir)
    if initial:
        ofn.lpstrInitialDir = initial
    try:
        ok = bool(_comdlg().GetOpenFileNameW(ctypes.byref(ofn)))
    except OSError:
        return None, "unavailable"
    if not ok:
        # Cancel vs error: CommDlgExtendedError == 0 means cancel.
        err = ctypes.windll.comdlg32.CommDlgExtendedError()
        return (None, "cancelled") if err == 0 else (None, "unavailable")
    path = buf.value.strip()
    if not path:
        return None, "cancelled"
    return path, "ok"


def choose_save_path_win(
    default_name: str, prompt: str = "选择保存位置"
) -> tuple[str | None, ChooseStatus]:
    name = (default_name or "export.txt").replace("/", os.sep)
    buf = ctypes.create_unicode_buffer(name, 32768)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile = len(buf)
    ofn.lpstrTitle = prompt or "选择保存位置"
    ofn.lpstrFilter = "All files (*.*)\0*.*\0\0"
    ofn.nFilterIndex = 1
    ofn.Flags = (
        OFN_EXPLORER
        | OFN_OVERWRITEPROMPT
        | OFN_PATHMUSTEXIST
        | OFN_HIDEREADONLY
        | OFN_NOCHANGEDIR
    )
    ext = Path(name).suffix.lstrip(".")
    if ext:
        ofn.lpstrDefExt = ext
    try:
        ok = bool(_comdlg().GetSaveFileNameW(ctypes.byref(ofn)))
    except OSError:
        return None, "unavailable"
    if not ok:
        err = ctypes.windll.comdlg32.CommDlgExtendedError()
        return (None, "cancelled") if err == 0 else (None, "unavailable")
    path = buf.value.strip()
    if not path:
        return None, "cancelled"
    return path, "ok"
