"""Application entrypoint.

Wires the :class:`AudioRecorder`, the parsed shortcuts and the platform-
appropriate :mod:`pynput` listener into a single blocking run loop.
"""

from __future__ import annotations

import os
import threading
import time

from .audio_recorder import AudioRecorder
from .config import default_config_path, load_config
from .hotkeys import build_listener
from .paste import paste_text
from .shortcuts import parse_shortcut
from .transcription import transcribe_file
from .ui import set_terminal_title


def main() -> None:
    """Load config, start the listener, and run until Ctrl+C."""
    cfg = load_config()
    config_path = default_config_path()

    set_terminal_title(f"{cfg.ui.title_ready} {cfg.ui.title_prefix}")

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

    recorder = AudioRecorder(cfg.audio, cfg.ui)
    toggle = parse_shortcut(cfg.shortcuts.toggle)
    cancel = parse_shortcut(cfg.shortcuts.cancel)

    def _on_complete(file_path: str) -> None:
        def _worker() -> None:
            try:
                text = transcribe_file(file_path, cfg.server)
                if text:
                    print(f"📝 Transkripsjon mottatt: \"{text}\"")
                    paste_text(text, cfg.paste)
            except Exception as e:
                print(f"❌ Det oppstod en feil under transkribering: {e}")
            finally:
                set_terminal_title(f"{cfg.ui.title_ready} {cfg.ui.title_prefix}")
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception as e:
                    print(f"Klarte ikke å slette midlertidig fil: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    listener = build_listener(recorder, toggle, cancel, cfg.behavior, _on_complete)
    listener.start()

    try:
        while True:
            time.sleep(cfg.behavior.main_loop_sleep_seconds)
    except KeyboardInterrupt:
        print("\nAvslutter talegjenkjenning...")
    finally:
        set_terminal_title(cfg.ui.title_prefix)
        listener.stop()


if __name__ == "__main__":
    main()