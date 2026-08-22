"""Application entrypoint.

Wires the :class:`AudioRecorder`, the parsed shortcuts and the platform-
appropriate :mod:`pynput` listener into a single blocking run loop.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time

from .audio_recorder import AudioRecorder
from .config import default_config_path, load_config
from .hotkeys import build_listener
from .paste import paste_text
from .platform_info import IS_WINDOWS
from .shortcuts import parse_shortcut
from .transcription import transcribe_file
from .ui import (
    StatusOverlay,
    get_foreground_window,
    set_foreground_window,
    setup_logging,
)


class OverlayRecorder:
    """Thin proxy around AudioRecorder that adds overlay state updates."""

    def __init__(self, recorder: AudioRecorder, overlay: StatusOverlay) -> None:
        self._recorder = recorder
        self._overlay = overlay
        self._target_hwnd: int | None = None

    @property
    def is_active(self) -> bool:
        return self._recorder.is_active

    def start(self, on_complete=None) -> None:  # type: ignore[override]
        self._target_hwnd = get_foreground_window()
        self._overlay.show_recording()
        self._recorder.start(on_complete)

    def stop(self) -> None:
        self._overlay.show_processing()
        self._recorder.stop()

    def cancel(self) -> None:
        self._overlay.show_cancelled()
        self._recorder.cancel()
        if self._target_hwnd:
            set_foreground_window(self._target_hwnd)

    def set_completion_callback(self, callback) -> None:
        self._recorder.set_completion_callback(callback)


def main() -> None:
    """Load config, start the listener, and run until Ctrl+C."""
    cfg = load_config()
    config_path = default_config_path()

    # Print startup banner to terminal BEFORE setup_logging redirects stdout.
    print("==================================================")
    print("🎙️  Enkel lokal talegjenkjenning startet!")
    print("==================================================")
    print(f"Konfigurasjon lastet fra: {config_path}")
    print(f"Server: {cfg.server.url}")
    print(f"Modell: {cfg.server.model}")
    print(f"Sample rate: {cfg.audio.sample_rate} Hz, kanaler: {cfg.audio.channels}")
    print("\nSnarvei registrert:")
    print(f"  - {cfg.shortcuts.toggle}  (start/stopp opptak)")
    print(f"  - {cfg.shortcuts.cancel}  (avbryt pågående opptak)")
    print(f"\nInnlimingsmetode: {cfg.paste.method}")
    print("(Trykk Ctrl + C i denne terminalen for å avslutte appen)")
    print("--------------------------------------------------")

    setup_logging(cfg)

    ui_queue: queue.Queue[callable] = queue.Queue()
    overlay = StatusOverlay(cfg.ui, ui_queue)

    recorder = AudioRecorder(cfg.audio, cfg.ui)
    wrapped = OverlayRecorder(recorder, overlay)
    toggle = parse_shortcut(cfg.shortcuts.toggle)
    cancel = parse_shortcut(cfg.shortcuts.cancel)

    def _on_complete(file_path: str) -> None:
        target = wrapped._target_hwnd
        logging.debug("_on_complete: target_hwnd=%s, file=%s", target, file_path)

        def _worker() -> None:
            try:
                text = transcribe_file(file_path, cfg.server)
                logging.debug("Transcription result: '%s...' (len=%d)", text[:80], len(text))
                if text:
                    logging.info("📝 Transkribert: %s", text)
                    if target:
                        logging.debug("Restoring focus to hwnd=%s", target)
                        set_foreground_window(target)
                    logging.debug("Pasting text via method=%s", cfg.paste.method)
                    paste_text(text, cfg.paste)
                    logging.debug("Paste completed")
                    overlay.show_done()
                else:
                    overlay.show_error("Ingen tale gjenkjent")
            except Exception as e:
                logging.error("Error in _worker: %s", e)
                overlay.show_error(str(e)[:60])
                if target:
                    set_foreground_window(target)
            finally:
                overlay.schedule_hide(cfg.ui.overlay_auto_hide_seconds)
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    listener = build_listener(wrapped, toggle, cancel, _on_complete)
    listener.start()

    if IS_WINDOWS:
        import ctypes

        _kernel32 = ctypes.windll.kernel32

        @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        def _console_ctrl_handler(ctrl_type):
            if ctrl_type in (0, 1):
                _shutdown.set()
                return 1
            return 0

        _keep_alive = _console_ctrl_handler  # prevent GC
        _kernel32.SetConsoleCtrlHandler(_console_ctrl_handler, 1)

    _shutdown = threading.Event()

    try:
        while not _shutdown.is_set():
            overlay.flush()
            overlay.root.update()
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


if __name__ == "__main__":
    main()
