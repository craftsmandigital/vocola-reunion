"""Deliver text to the active application.

Four methods are supported (selected by :attr:`PasteConfig.method`):

* ``direct`` — type characters one by one via pynput. The clipboard is
  never touched. Slow for long text and may drop Unicode / emoji.
* ``ctrl_v`` — set clipboard privately, then send Ctrl+V.
* ``ctrl_shift_v`` — same, but sends Ctrl+Shift+V (pastes as plain text
  in many apps).
* ``shift_insert`` — same, but sends Shift+Insert.

Before pasting, the current clipboard text is snapshotted so it can be
restored afterwards (controlled by :attr:`PasteConfig.restore_clipboard`).
On Windows the clipboard writes are made "private" via the privacy
formats (see :mod:`speech_to_text.clipboard`) so the dictated text doesn't
end up in ``Win+V`` history.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pynput import keyboard
from pynput.keyboard import Controller, Key

from .clipboard import get_clipboard_text, restore_clipboard, set_clipboard_text
from .config import PasteConfig


PasteFn = Callable[[Controller], None]


def _paste_ctrl_v(controller: Controller) -> None:
    with controller.pressed(Key.ctrl):
        controller.press("v")
        controller.release("v")


def _paste_ctrl_shift_v(controller: Controller) -> None:
    with controller.pressed(Key.ctrl):
        with controller.pressed(Key.shift):
            controller.press("v")
            controller.release("v")


def _paste_shift_insert(controller: Controller) -> None:
    with controller.pressed(Key.shift):
        controller.press(Key.insert)
        controller.release(Key.insert)


# Lookup table for the clipboard-based methods. ``direct`` is special-cased
# below because it bypasses the clipboard entirely.
_CLIPBOARD_PASTE_METHODS: dict[str, PasteFn] = {
    "ctrl_v": _paste_ctrl_v,
    "ctrl_shift_v": _paste_ctrl_shift_v,
    "shift_insert": _paste_shift_insert,
}


def _paste_direct(controller: Controller, text: str) -> None:
    controller.type(text)


def paste_text(text: str, cfg: PasteConfig) -> None:
    """Deliver ``text`` to the focused application using ``cfg.method``.

    The clipboard is snapshotted, then either written-to-then-pasted-from
    (the three ``ctrl_v``/``ctrl_shift_v``/``shift_insert`` methods) or
    typed directly (``direct``). If ``cfg.restore_clipboard`` is true the
    snapshot is put back after a short delay; otherwise the clipboard is
    left as-is (still private, so dictation text doesn't leak to history).
    """
    if cfg.method == "direct":
        _paste_direct(Controller(), text)
        return

    snapshot = get_clipboard_text() if cfg.restore_clipboard else None
    controller = Controller()
    paste_fn = _CLIPBOARD_PASTE_METHODS.get(cfg.method)

    try:
        set_clipboard_text(text, private=cfg.exclude_from_history)
        if cfg.paste_delay > 0:
            time.sleep(cfg.paste_delay)

        if paste_fn is None:
            print(f"⚠️ Ukjent paste-metode '{cfg.method}', faller tilbake til ctrl_v.")
            _paste_ctrl_v(controller)
        else:
            paste_fn(controller)
    finally:
        if cfg.restore_clipboard:
            time.sleep(cfg.restore_delay)
            restore_clipboard(snapshot)