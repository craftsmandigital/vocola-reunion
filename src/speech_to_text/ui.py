"""Terminal title helpers.

Sets the console window title so the user can see the current state at a
glance (``🔵 [KLAR]``, ``🔴 [OPPTAK...]``, ``⏳ [BEHANDLER...]``).
"""

from __future__ import annotations

import sys

from .platform_info import IS_WINDOWS, get_kernel32


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
        sys.stdout.write(f"\x1b]2;{title}\x07")
        sys.stdout.flush()
    except Exception:
        pass