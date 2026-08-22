"""Audio capture state machine.

Owns the microphone stream, the worker thread that drains the audio queue
into a temporary WAV file, and the lifecycle flags that used to be scattered
across module-level globals in the original ``main.py``.

The recorder is intentionally unaware of transcription or paste: when the
user stops recording, the recorder invokes ``on_complete(path)`` with the
path to the freshly-written WAV. That callback is responsible for
transcribing and pasting.
"""

from __future__ import annotations

import logging
import queue
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path


logger = logging.getLogger(__name__)

import sounddevice as sd
import soundfile as sf

from .config import AudioConfig, UiConfig
from .ui import set_terminal_title


AudioCallback = Callable[[str], None]


class AudioRecorder:
    """Manages microphone capture for a single dictation session.

    A recorder can be ``start()``-ed and ``stop()``-ped many times. Between
    sessions ``is_active`` is ``False`` and there are no running threads or
    open streams. ``cancel()`` aborts an in-progress session without invoking
    the completion callback.
    """

    def __init__(self, audio_cfg: AudioConfig, ui_cfg: UiConfig) -> None:
        self._audio_cfg = audio_cfg
        self._ui_cfg = ui_cfg
        self._is_recording = False
        self._stream: sd.InputStream | None = None
        self._writer_thread: threading.Thread | None = None
        self._audio_queue: queue.Queue = queue.Queue()
        self._temp_file_path: str | None = None
        self._on_complete: AudioCallback | None = None

    @property
    def is_active(self) -> bool:
        """``True`` while a recording session is in progress."""
        return self._is_recording

    def set_completion_callback(self, on_complete: AudioCallback) -> None:
        """Install the callback used by future :meth:`start` calls.

        Useful on platforms where the hotkey listener is created before
        any recording starts and the callback needs to reference runtime
        state (e.g. the loaded :class:`AppConfig`).
        """
        self._on_complete = on_complete

    def _put_audio(self, indata, frames, time_info, status) -> None:
        """sounddevice callback: enqueue each block for the writer thread."""
        if status:
            logger.debug("Audio callback status: %s", status)
        self._audio_queue.put(indata.copy())

    def _write_to_file(self) -> None:
        """Worker thread target: drain the audio queue into the temp WAV."""
        assert self._temp_file_path is not None
        with sf.SoundFile(
            self._temp_file_path,
            mode="w",
            samplerate=self._audio_cfg.sample_rate,
            channels=self._audio_cfg.channels,
        ) as wav_file:
            while self._is_recording or not self._audio_queue.empty():
                try:
                    data = self._audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                wav_file.write(data)

    def start(self, on_complete: AudioCallback | None = None) -> None:
        """Begin a new recording session.

        ``on_complete`` is invoked from a background thread once the WAV file
        has been fully written; it receives the file path. When omitted, the
        callback installed via :meth:`set_completion_callback` is used. The
        callback persists across sessions, so a recorder can be started and
        stopped any number of times. Calling ``start`` while already
        recording is a no-op.
        """
        if self._is_recording:
            return

        if on_complete is not None:
            self._on_complete = on_complete
        if self._on_complete is None:
            raise RuntimeError(
                "Ingen fullførings-callback satt; installer en via set_completion_callback()."
            )

        self._is_recording = True
        self._audio_queue = queue.Queue()

        set_terminal_title(f"{self._ui_cfg.title_recording} {self._ui_cfg.title_prefix}")

        temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        self._temp_file_path = temp_file.name
        temp_file.close()

        self._stream = sd.InputStream(
            samplerate=self._audio_cfg.sample_rate,
            channels=self._audio_cfg.channels,
            callback=self._put_audio,
        )
        self._stream.start()

        self._writer_thread = threading.Thread(target=self._write_to_file, daemon=True)
        self._writer_thread.start()

    def stop(self) -> None:
        """Stop the current session and hand the WAV to the completion callback.

        Safe to call when no session is active.
        """
        if not self._is_recording:
            return

        self._is_recording = False

        set_terminal_title(f"{self._ui_cfg.title_processing} {self._ui_cfg.title_prefix}")

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if self._writer_thread is not None:
            self._writer_thread.join()
            self._writer_thread = None

        path = self._temp_file_path
        callback = self._on_complete
        self._temp_file_path = None
        if path is not None and callback is not None:
            threading.Thread(target=callback, args=(path,), daemon=True).start()

    def cancel(self) -> None:
        """Abort the current session and delete the partial WAV.

        Unlike :meth:`stop`, the completion callback is **not** invoked.
        """
        if not self._is_recording:
            return

        self._is_recording = False

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if self._writer_thread is not None:
            self._writer_thread.join()
            self._writer_thread = None

        path = self._temp_file_path
        self._temp_file_path = None
        if path is not None and Path(path).exists():
            try:
                Path(path).unlink()
            except Exception as e:
                logger.warning("Klarte ikke å slette avbrutt opptak: %s", e)

        set_terminal_title(f"{self._ui_cfg.title_ready} {self._ui_cfg.title_prefix}")