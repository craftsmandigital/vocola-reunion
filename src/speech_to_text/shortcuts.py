"""Shortcut string parsing.

The same pynput-style strings (``"<ctrl>+<shift>+i"``, ``"<esc>"``) are used
by both :class:`pynput.keyboard.GlobalHotKeys` (macOS/Linux) and the Windows
low-level event filter. This module parses them once into a small typed
record so each consumer can pick what it needs.
"""

from __future__ import annotations

from dataclasses import dataclass


# Virtual-key codes for ASCII A–Z. The Windows event filter uses these to
# compare against ``data.vkCode`` for the main key of each shortcut.
VK_A: int = 0x41
VK_Z: int = 0x5A
VK_ESCAPE: int = 0x1B


_MODIFIER_TOKENS: dict[str, str] = {
    "<ctrl>": "ctrl",
    "ctrl": "ctrl",
    "control": "ctrl",
    "<shift>": "shift",
    "shift": "shift",
    "<alt>": "alt",
    "alt": "alt",
}


@dataclass(frozen=True)
class Shortcut:
    """A parsed shortcut specification.

    ``modifiers`` is a normalised set of ``{"ctrl", "shift", "alt"}`` and
    ``main_key`` is the bare key name (e.g. ``"i"``, ``"escape"``).
    ``vk_code`` is the Windows virtual-key code for ``main_key`` when it is a
    single ASCII letter or ``escape``/``esc``; ``None`` for anything else.
    """

    modifiers: frozenset[str]
    main_key: str
    vk_code: int | None


def _vk_for_main_key(main_key: str) -> int | None:
    """Return the Windows VK code for a single-key shortcut name."""
    if len(main_key) == 1 and main_key.isalpha():
        return VK_A + (ord(main_key.upper()) - ord("A"))
    if main_key in ("escape", "esc"):
        return VK_ESCAPE
    return None


def parse_shortcut(spec: str) -> Shortcut:
    """Parse ``"<ctrl>+<shift>+i"`` (or ``"<esc>"``) into a :class:`Shortcut`.

    Order is irrelevant: ``"<shift>+<ctrl>+i"`` is treated the same as
    ``"<ctrl>+<shift>+i"``. Modifier names are matched case-insensitively and
    accept both ``<ctrl>``/``ctrl``/``control`` spellings.
    """
    modifiers: set[str] = set()
    main_key: str | None = None

    for raw in spec.split("+"):
        part = raw.strip().lower()
        if part in _MODIFIER_TOKENS:
            modifiers.add(_MODIFIER_TOKENS[part])
        else:
            main_key = part.strip("<>")

    if main_key is None:
        raise ValueError(f"Invalid shortcut (no main key): {spec!r}")

    return Shortcut(
        modifiers=frozenset(modifiers),
        main_key=main_key,
        vk_code=_vk_for_main_key(main_key),
    )