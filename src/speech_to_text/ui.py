"""Status overlay and file logging.

Provides a small, always-on-top tkinter overlay that shows the current
dictation state (recording / processing / done / cancelled / error).
All ``print()`` output is tee'd to a log file in the project root.
"""

from __future__ import annotations

import logging
import queue
import sys
import tkinter as tk
from pathlib import Path
from typing import TYPE_CHECKING

from .platform_info import IS_WINDOWS, get_kernel32, get_user32

if TYPE_CHECKING:
    from .config import UiConfig


logger = logging.getLogger(__name__)


def set_terminal_title(title: str) -> None:
    """Set the terminal window title (best-effort, swallows errors).

    On Windows this uses ``kernel32.SetConsoleTitleW``. On Linux/macOS it
    emits the standard OSC 2 escape sequence. Both paths are fire-and-forget;
    failure to set the title must never break the app.
    """
    if IS_WINDOWS:
        try:
            kernel32 = get_kernel32()
            kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
        return

    try:
        import sys as _sys
        _sys.stdout.write(f"\x1b]2;{title}\x07")
        _sys.stdout.flush()
    except Exception:
        pass


_log_configured = False


def setup_logging(cfg=None) -> None:
    """Configure logging with separate console and file handlers.

    Console: configurable level (default INFO) - shows status + transcription
    File: configurable level (default DEBUG) - shows everything
    """
    global _log_configured
    if _log_configured:
        return
    _log_configured = True

    # Default config if not provided
    console_level = "INFO"
    file_level = "DEBUG"
    file_name = "speech-to-text.log"

    if cfg and hasattr(cfg, 'logging'):
        console_level = getattr(cfg.logging, 'console_level', 'INFO')
        file_level = getattr(cfg.logging, 'file_level', 'DEBUG')
        file_name = getattr(cfg.logging, 'file_name', 'speech-to-text.log')

    log_path = Path(__file__).resolve().parents[2] / file_name

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture all levels

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler - only INFO and above
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, console_level.upper(), logging.INFO))
    console_formatter = logging.Formatter("%(message)s")
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # File handler - DEBUG and above
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(getattr(logging, file_level.upper(), logging.DEBUG))
    file_formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Also capture warnings
    logging.captureWarnings(True)


# ── Colour palette ─────────────────────────────────────────────────

_BG_IDLE = "#1e1e1e"
_BG_RECORDING = "#8B0000"
_BG_PROCESSING = "#CC7700"
_BG_DONE = "#006400"
_BG_ERROR = "#8B0000"


class StatusOverlay:
    """Small, always-on-top status overlay built with tkinter.

    The window is frameless (``overrideredirect``), semi-transparent and
    positioned in the bottom-right corner of the primary screen.  It does
    **not** steal focus from the active application, so pasted text still
    lands where the user expects.
    """

    _WIDTH = 280
    _HEIGHT = 48
    _MARGIN = 20
    _FONT_FAMILY = "Segoe UI"
    _FONT_SIZE = 12

    def __init__(self, cfg: UiConfig, q: queue.Queue[callable]) -> None:
        self._cfg = cfg
        self._q = q
        self._hide_job: str | None = None

        self._root = tk.Tk()
        self._root.withdraw()
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.attributes("-alpha", cfg.overlay_opacity)
        self._root.configure(bg=_BG_IDLE)

        # Position: bottom-right corner, above taskbar
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        x = sw - self._WIDTH - self._MARGIN
        y = sh - self._HEIGHT - self._MARGIN - 40  # 40 ≈ taskbar height
        self._root.geometry(f"{self._WIDTH}x{self._HEIGHT}+{x}+{y}")

        self._label = tk.Label(
            self._root,
            text="",
            font=(self._FONT_FAMILY, self._FONT_SIZE, "bold"),
            bg=_BG_IDLE,
            fg="white",
            padx=14,
            pady=6,
            anchor="w",
        )
        self._label.pack(fill="both", expand=True)

    # ── Public API ──────────────────────────────────────────────────

    @property
    def root(self) -> tk.Tk:
        return self._root

    def show_recording(self) -> None:
        def _do() -> None:
            self._cancel_pending_hide()
            self._set(self._cfg.overlay_recording, _BG_RECORDING)
            self._show()
        self._q.put(_do)

    def show_processing(self) -> None:
        def _do() -> None:
            self._cancel_pending_hide()
            self._set(self._cfg.overlay_processing, _BG_PROCESSING)
            self._show()
        self._q.put(_do)

    def show_done(self, text: str = "") -> None:
        def _do() -> None:
            self._cancel_pending_hide()
            self._set(self._cfg.overlay_done, _BG_DONE)
            self._show()
            self._schedule_hide(self._cfg.overlay_auto_hide_seconds)
        self._q.put(_do)

    def show_cancelled(self) -> None:
        def _do() -> None:
            self._cancel_pending_hide()
            self._set(self._cfg.overlay_cancelled, _BG_ERROR)
            self._show()
            self._schedule_hide(1.5)
        self._q.put(_do)

    def show_error(self, text: str = "") -> None:
        def _do() -> None:
            display = f"{self._cfg.overlay_error} {text[:40]}" if text else self._cfg.overlay_error
            self._cancel_pending_hide()
            self._set(display, _BG_ERROR)
            self._show()
            self._schedule_hide(3.0)
        self._q.put(_do)

    def hide(self) -> None:
        def _do() -> None:
            self._cancel_pending_hide()
            self._root.withdraw()
        self._q.put(_do)

    def schedule_hide(self, delay: float) -> None:
        self._q.put(lambda: self._schedule_hide(delay))

    def flush(self) -> None:
        """Execute all queued tkinter tasks — call from the main thread."""
        while True:
            try:
                task = self._q.get_nowait()
            except queue.Empty:
                break
            task()

    def destroy(self) -> None:
        self._cancel_pending_hide()
        self._root.destroy()

    # ── Internals ───────────────────────────────────────────────────

    def _set(self, text: str, bg: str) -> None:
        self._label.config(text=text, bg=bg)

    def _show(self) -> None:
        # Use deiconify() which works reliably for overrideredirect windows
        # on all platforms. The -topmost attribute keeps it above other windows
        # without stealing focus.
        self._root.deiconify()
        self._root.attributes("-topmost", True)

    def _schedule_hide(self, delay: float) -> None:
        self._cancel_pending_hide()
        self._hide_job = self._root.after(int(delay * 1000), self._root.withdraw)

    def _cancel_pending_hide(self) -> None:
        if self._hide_job is not None:
            self._root.after_cancel(self._hide_job)
            self._hide_job = None


# ── Windows focus helpers ──────────────────────────────────────────

_api_configured = False

_SW_RESTORE = 9


def _configure_window_api() -> None:
    """Set ctypes signatures for the window functions we use (Windows only)."""
    global _api_configured
    if _api_configured:
        return
    _api_configured = True

    import ctypes

    user32 = get_user32()
    kernel32 = get_kernel32()

    HWND = ctypes.c_void_p
    BOOL = ctypes.c_bool
    DWORD = ctypes.c_uint
    UINT = ctypes.c_uint

    user32.GetForegroundWindow.restype = HWND
    user32.GetForegroundWindow.argtypes = []
    user32.SetForegroundWindow.restype = BOOL
    user32.SetForegroundWindow.argtypes = [HWND]
    user32.IsIconic.restype = BOOL
    user32.IsIconic.argtypes = [HWND]
    user32.IsWindow.restype = BOOL
    user32.IsWindow.argtypes = [HWND]
    user32.ShowWindow.restype = BOOL
    user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
    user32.AttachThreadInput.restype = BOOL
    user32.AttachThreadInput.argtypes = [DWORD, DWORD, BOOL]
    user32.BringWindowToTop.restype = BOOL
    user32.BringWindowToTop.argtypes = [HWND]
    user32.GetWindowThreadProcessId.restype = DWORD
    user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]

    kernel32.GetCurrentThreadId.restype = DWORD
    kernel32.GetCurrentThreadId.argtypes = []


def get_foreground_window() -> int | None:
    """Return the currently focused top-level window's HWND (Windows only)."""
    if not IS_WINDOWS:
        return None
    try:
        _configure_window_api()
        hwnd = get_user32().GetForegroundWindow()
        return hwnd if hwnd else None
    except Exception:
        return None


def set_foreground_window(hwnd: int | None) -> None:
    """Give ``hwnd`` focus and bring it to the top (Windows only, best-effort).

    Uses ``AttachThreadInput`` to bypass Windows foreground restrictions.
    Only restores from minimised state; maximised windows are left as-is.
    """
    if not IS_WINDOWS or not hwnd:
        logger.debug("set_foreground_window: skipped (IS_WINDOWS=%s, hwnd=%s)", IS_WINDOWS, hwnd)
        return
    try:
        _configure_window_api()
        user32 = get_user32()
        kernel32 = get_kernel32()

        if not user32.IsWindow(hwnd):
            logger.debug("set_foreground_window: hwnd=%s is not a valid window", hwnd)
            return

        if user32.IsIconic(hwnd):
            logger.debug("set_foreground_window: hwnd=%s is minimized, restoring", hwnd)
            user32.ShowWindow(hwnd, _SW_RESTORE)

        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        current_thread = kernel32.GetCurrentThreadId()
        logger.debug("set_foreground_window: target_thread=%s, current_thread=%s", target_thread, current_thread)
        attached = False
        if target_thread:
            attached = bool(
                user32.AttachThreadInput(current_thread, target_thread, True)
            )
            logger.debug("set_foreground_window: AttachThreadInput=%s", attached)
        try:
            user32.BringWindowToTop(hwnd)
            result = user32.SetForegroundWindow(hwnd)
            logger.debug("set_foreground_window: SetForegroundWindow result=%s", result)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, target_thread, False)
    except Exception as e:
        logger.debug("set_foreground_window: exception: %s", e)
