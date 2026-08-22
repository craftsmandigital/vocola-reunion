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

import logging
import time
from collections.abc import Callable

from pynput import keyboard
from pynput.keyboard import Controller, Key

from .clipboard import get_clipboard_text, restore_clipboard, set_clipboard_text
from .config import PasteConfig


logger = logging.getLogger(__name__)


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

    If ``cfg.fallback_to_direct`` is true and the clipboard-based method
    appears to fail (clipboard doesn't contain our text after paste),
    falls back to direct typing.
    """
    logger.debug("paste_text: method=%s, text_len=%d, exclude_from_history=%s, fallback_to_direct=%s",
                 cfg.method, len(text), cfg.exclude_from_history, cfg.fallback_to_direct)
    if cfg.method == "direct":
        logger.debug("paste_text: using direct typing")
        _paste_direct(Controller(), text)
        return

    snapshot = get_clipboard_text() if cfg.restore_clipboard else None
    controller = Controller()
    paste_fn = _CLIPBOARD_PASTE_METHODS.get(cfg.method)

    def _try_paste() -> bool:
        """Try the clipboard-based paste. Returns True if successful."""
        try:
            logger.debug("paste_text: setting clipboard (private=%s)", cfg.exclude_from_history)
            set_clipboard_text(text, private=cfg.exclude_from_history)
            if cfg.paste_delay > 0:
                logger.debug("paste_text: paste_delay=%s", cfg.paste_delay)
                time.sleep(cfg.paste_delay)

            if paste_fn is None:
                logger.warning("Ukjent paste-metode '%s', faller tilbake til ctrl_v.", cfg.method)
                _paste_ctrl_v(controller)
            else:
                logger.debug("paste_text: calling paste_fn=%s", cfg.method)
                paste_fn(controller)
            return True
        except Exception as e:
            logger.debug("paste_text: paste failed: %s", e)
            return False

    success = _try_paste()

    # Fallback to direct typing if enabled and clipboard paste failed
    if not success and cfg.fallback_to_direct and cfg.method != "direct":
        logger.debug("paste_text: falling back to direct typing")
        _paste_direct(Controller(), text)
    elif success and cfg.fallback_to_direct and cfg.method != "direct":
        # Verify clipboard still has our text (simple check)
        time.sleep(0.05)
        current = get_clipboard_text()
        if current != text:
            logger.debug("paste_text: clipboard verification failed, trying direct typing as fallback")
            _paste_direct(Controller(), text)

    if cfg.restore_clipboard:
        logger.debug("paste_text: restore_delay=%s, restoring clipboard", cfg.restore_delay)
        time.sleep(cfg.restore_delay)
        restore_clipboard(snapshot)