"""Global hotkey wiring.

On Windows the listener is a low-level :class:`pynput.keyboard.Listener`
with a ``win32_event_filter`` that we own, because pynput's
``GlobalHotKeys`` doesn't give us the per-keystroke suppression we need to
keep the shortcut from also opening DevTools (in browsers) or closing
dialogs (Escape).

On macOS / Linux the listener is a :class:`pynput.keyboard.GlobalHotKeys`,
which is enough because the OS routes the shortcut to us directly.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable

from pynput import keyboard

from .audio_recorder import AudioRecorder
from .config import BehaviorConfig
from .platform_info import IS_WINDOWS, VK_CONTROL, VK_SHIFT, get_user32
from .shortcuts import Shortcut


# pynput's win32_event_filter expects this return value (and these specific
# message numbers).
_MSG_KEYDOWN_WINDOWS = 256
_MSG_KEYUP_WINDOWS = 257
_MSG_SYSKEYDOWN_WINDOWS = 260
_MSG_SYSKEYUP_WINDOWS = 261


class WindowsEventFilter:
    """Low-level Windows keyboard filter for the toggle / cancel shortcuts.

    Holds the recorder and the toggle debounce state that used to live as
    module-level globals. The filter ``suppress_event``-es the shortcut
    keystrokes so the focused application never sees them.
    """

    def __init__(
        self,
        recorder: AudioRecorder,
        toggle: Shortcut,
        cancel: Shortcut,
        behavior: BehaviorConfig,
    ) -> None:
        self._recorder = recorder
        self._toggle = toggle
        self._cancel = cancel
        self._toggle_debounce = behavior.toggle_debounce_seconds
        self._listener: keyboard.Listener | None = None

        self._last_press_time: float = 0.0
        self._escape_was_cancelled: bool = False

    def bind(self, listener: keyboard.Listener) -> None:
        """Attach the listener so we can suppress events from inside the filter."""
        self._listener = listener

    def __call__(self, msg: int, data) -> bool:
        """Hook callback. Return ``True`` to keep the listener alive."""
        try:
            self._handle(msg, data)
        except Exception as e:
            if "SuppressException" in type(e).__name__:
                raise
            print(f"DEBUG-FEIL i tastaturfilter: {e}", file=sys.stderr)
        return True

    def _handle(self, msg: int, data) -> None:
        if not (IS_WINDOWS and self._toggle.vk_code is not None):
            return

        user32 = get_user32()

        if data.vkCode == self._toggle.vk_code:
            ctrl_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
            shift_down = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)

            if ctrl_down and shift_down and msg in (_MSG_KEYDOWN_WINDOWS, _MSG_SYSKEYDOWN_WINDOWS):
                now = time.time()
                if now - self._last_press_time > self._toggle_debounce:
                    self._last_press_time = now
                    print("⚡ [SNARVEI DETEKTERT] Veksler opptak...")
                    self._toggle_recording()

            if self._listener is not None:
                self._listener.suppress_event()

        elif data.vkCode == self._cancel.vk_code and self._cancel.vk_code is not None:
            if self._recorder.is_active or self._escape_was_cancelled:
                if msg in (_MSG_KEYDOWN_WINDOWS, _MSG_SYSKEYDOWN_WINDOWS):
                    self._escape_was_cancelled = True
                    self._recorder.cancel()
                elif msg in (_MSG_KEYUP_WINDOWS, _MSG_SYSKEYUP_WINDOWS):
                    self._escape_was_cancelled = False

                if self._listener is not None:
                    self._listener.suppress_event()

    def _toggle_recording(self) -> None:
        if self._recorder.is_active:
            self._recorder.stop()
        else:
            self._recorder.start()


def build_listener(
    recorder: AudioRecorder,
    toggle: Shortcut,
    cancel: Shortcut,
    behavior: BehaviorConfig,
    on_complete: Callable[[str], None],
) -> keyboard.Listener | keyboard.GlobalHotKeys:
    """Construct the right pynput listener for the current platform.

    On Windows a :class:`WindowsEventFilter` is wired into a
    :class:`keyboard.Listener`. On other platforms a
    :class:`keyboard.GlobalHotKeys` is returned. The ``on_complete`` callback
    is installed on the recorder so future :meth:`AudioRecorder.start` calls
    know where to hand the WAV file.
    """
    recorder.set_completion_callback(on_complete)

    if IS_WINDOWS:
        filter_ = WindowsEventFilter(recorder, toggle, cancel, behavior)
        listener = keyboard.Listener(win32_event_filter=filter_)
        filter_.bind(listener)
        return listener

    # macOS / Linux path.
    return keyboard.GlobalHotKeys(
        {
            _pynput_key(toggle): _toggle_factory(recorder),
            _pynput_key(cancel): _cancel_factory(recorder),
        }
    )


def _pynput_key(shortcut: Shortcut) -> str:
    """Re-stringify a :class:`Shortcut` in pynput format.

    The original ``config.yaml`` already stores pynput-style strings, so we
    just round-trip them: the parsed :class:`Shortcut` is only used to look
    up the Windows VK code. We re-stringify here from the original string
    spec kept on the dataclass.
    """
    # The string is preserved alongside the parsed view in shortcuts.py via
    # the original spec — for simplicity we accept either the parsed
    # ``Shortcut`` here by reconstructing from its parts.
    parts = []
    for mod in ("ctrl", "alt", "shift"):
        if mod in shortcut.modifiers:
            parts.append(f"<{mod}>")
    parts.append(f"<{shortcut.main_key}>")
    return "+".join(parts)


def _toggle_factory(recorder: AudioRecorder) -> Callable[[], None]:
    def _toggle() -> None:
        if recorder.is_active:
            recorder.stop()
        else:
            recorder.start()

    return _toggle


def _cancel_factory(recorder: AudioRecorder) -> Callable[[], None]:
    def _cancel() -> None:
        recorder.cancel()

    return _cancel