"""Clipboard read/write/restore.

On Windows the helpers below use raw ``ctypes`` calls against ``user32`` and
``kernel32`` so they can attach the four ``Exclude*``/``CanInclude*``/
``CanUpload*``/``Clipboard Viewer Ignore`` privacy formats. Those formats
make the clipboard entry invisible to Windows' ``Win+V`` history and cloud
sync, matching the behaviour of KeePass and Chrome Incognito.

On non-Windows platforms the helpers fall back to :mod:`pyperclip`.
"""

from __future__ import annotations

import ctypes
import struct
import time

import pyperclip

from .platform_info import IS_WINDOWS, get_kernel32, get_user32


# Clipboard format constants used by the Windows helpers.
CF_UNICODETEXT: int = 13
GMEM_MOVEABLE: int = 0x0002

# Privacy formats — same set the original main.py used.
_PRIVACY_FORMATS: list[str] = [
    "ExcludeClipboardContentFromMonitorProcessing",
    "CanIncludeInClipboardHistory",
    "CanUploadToCloudClipboard",
    "Clipboard Viewer Ignore",
]

# Tracks whether we've already printed the "privacy formats OK" banner so it
# only shows once per session.
_privacy_ok_reported: bool = False


def _open_clipboard_with_retry(timeout: float = 3.0) -> bool:
    """Open the clipboard with retry to ride out "clipboard busy" errors."""
    user32 = get_user32()
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]

    deadline = time.time() + timeout
    while True:
        if user32.OpenClipboard(None):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.05)


def _verify_privacy_formats_windows() -> list[str]:
    """Return the list of privacy formats that are NOT present in the clipboard.

    The caller passes each name through ``RegisterClipboardFormatW`` and then
    asks ``IsClipboardFormatAvailable`` whether Windows knows about it on the
    currently-open clipboard. Anything missing means the OS / target app
    didn't honour the privacy tag.
    """
    user32 = get_user32()
    user32.RegisterClipboardFormatW.restype = ctypes.c_uint
    user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
    user32.IsClipboardFormatAvailable.restype = ctypes.c_bool
    user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]

    if not _open_clipboard_with_retry():
        return list(_PRIVACY_FORMATS)

    missing: list[str] = []
    try:
        for name in _PRIVACY_FORMATS:
            fmt = user32.RegisterClipboardFormatW(name)
            if not fmt or not user32.IsClipboardFormatAvailable(fmt):
                missing.append(name)
    finally:
        user32.CloseClipboard()
    return missing


def _set_dword(kernel32, user32, fmt: int, value: int) -> None:
    """Helper: write a single DWORD (4-byte little-endian) into a clipboard slot."""
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, 4)
    if not h:
        raise ctypes.WinError()
    ptr = kernel32.GlobalLock(h)
    if not ptr:
        raise ctypes.WinError()
    ctypes.memmove(ptr, struct.pack("<I", value), 4)
    kernel32.GlobalUnlock(h)
    if not user32.SetClipboardData(fmt, h):
        raise ctypes.WinError()


def _set_clipboard_private_windows(text: str) -> None:
    """Set ``text`` on the clipboard with the four privacy format tags."""
    user32 = get_user32()
    kernel32 = get_kernel32()

    user32.RegisterClipboardFormatW.restype = ctypes.c_uint
    user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.EmptyClipboard.restype = ctypes.c_bool
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.CloseClipboard.restype = ctypes.c_bool
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    def register(name: str) -> int:
        return user32.RegisterClipboardFormatW(name)

    if not _open_clipboard_with_retry():
        raise ctypes.WinError()
    try:
        if not user32.EmptyClipboard():
            raise ctypes.WinError()

        payload = text.encode("utf-16-le") + b"\x00\x00"
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not h:
            raise ctypes.WinError()
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, payload, len(payload))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            raise ctypes.WinError()

        _set_dword(kernel32, user32, register("ExcludeClipboardContentFromMonitorProcessing"), 1)
        _set_dword(kernel32, user32, register("CanIncludeInClipboardHistory"), 0)
        _set_dword(kernel32, user32, register("CanUploadToCloudClipboard"), 0)
        _set_dword(kernel32, user32, register("Clipboard Viewer Ignore"), 1)
    finally:
        user32.CloseClipboard()


def _get_clipboard_text_windows() -> str | None:
    """Return the current CF_UNICODETEXT on the clipboard, or ``None``."""
    user32 = get_user32()
    kernel32 = get_kernel32()

    user32.OpenClipboard.restype = ctypes.c_bool
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.IsClipboardFormatAvailable.restype = ctypes.c_bool
    user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    user32.CloseClipboard.restype = ctypes.c_bool
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    if not _open_clipboard_with_retry():
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        size = kernel32.GlobalSize(h)
        if not size:
            return None
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return None
        try:
            raw = ctypes.string_at(ptr, size)
        finally:
            kernel32.GlobalUnlock(h)
        return raw.decode("utf-16-le").rstrip("\x00")
    finally:
        user32.CloseClipboard()


def _empty_clipboard_windows() -> bool:
    """Empty the clipboard. Returns ``True`` on success, ``False`` if the clipboard stayed busy."""
    user32 = get_user32()
    user32.EmptyClipboard.restype = ctypes.c_bool

    if not _open_clipboard_with_retry():
        return False
    try:
        return bool(user32.EmptyClipboard())
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str, private: bool = True) -> None:
    """Place ``text`` on the clipboard.

    On Windows with ``private=True`` the four privacy formats are set so the
    text doesn't end up in ``Win+V`` history or cloud sync. On any failure
    the function falls back to :func:`pyperclip.copy`. The "privacy OK"
    banner is printed at most once per session.
    """
    global _privacy_ok_reported

    if IS_WINDOWS and private:
        try:
            _set_clipboard_private_windows(text)
            missing = _verify_privacy_formats_windows()
            if missing:
                print(f"⚠️ Privacy-formater MANGLER: {', '.join(missing)}")
            elif not _privacy_ok_reported:
                _privacy_ok_reported = True
                print("✔ Privacy-formater bekreftet (4/4) — teksten skjules for Win+V.")
            return
        except Exception as e:
            print(f"⚠️ Kunne ikke sette privat utklippstavle, bruker vanlig kopi: {e}")

    pyperclip.copy(text)


def get_clipboard_text() -> str | None:
    """Snapshot the current clipboard text, or ``None`` if empty / not text."""
    if IS_WINDOWS:
        try:
            return _get_clipboard_text_windows()
        except Exception as e:
            print(f"⚠️ Kunne ikke lese utklippstavlen: {e}")
            return None
    try:
        return pyperclip.paste()
    except Exception:
        return None


def restore_clipboard(snapshot: str | None) -> None:
    """Restore the clipboard to ``snapshot`` (taken before a paste).

    Passing ``None`` empties the clipboard instead — this is used when the
    previous contents weren't text. Restored contents are written privately
    on Windows so they don't show up in ``Win+V`` either.
    """
    if IS_WINDOWS:
        if snapshot is None:
            if not _empty_clipboard_windows():
                print("⚠️ Kunne ikke tømme utklippstavlen (opptatt etter 3 s?).")
            return
        try:
            _set_clipboard_private_windows(snapshot)
            return
        except Exception as e:
            print(f"⚠️ Kunne ikke gjenopprette utklippstavlen: {e}")
            return

    if snapshot is not None:
        pyperclip.copy(snapshot)