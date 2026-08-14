"""Platform detection and lazy Windows API access.

The original module imported :mod:`ctypes` eagerly on Windows and re-resolved
``user32``/``kernel32`` inside every clipboard helper. Centralising that here
removes the repetition while preserving behaviour: ``IS_WINDOWS`` is set once
at import, ``user32`` is only touched when a Windows-only path runs.
"""

from __future__ import annotations

import platform
from typing import Any

IS_WINDOWS: bool = platform.system() == "Windows"

# Virtual-key codes used by the shortcut parser and the low-level event filter.
VK_CONTROL: int = 0x11
VK_SHIFT: int = 0x10


def get_user32() -> Any:
    """Return the ``user32`` ctypes binding (Windows only).

    Callers must gate themselves on :data:`IS_WINDOWS`. The first call imports
    :mod:`ctypes`; subsequent calls return the cached binding.
    """
    import ctypes  # local import: keeps non-Windows startup ctypes-free

    return ctypes.windll.user32


def get_kernel32() -> Any:
    """Return the ``kernel32`` ctypes binding (Windows only)."""
    import ctypes

    return ctypes.windll.kernel32