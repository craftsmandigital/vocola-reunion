"""In-memory PCM capture for command mode.

Duck-type compatible with :class:`speech_to_text.audio_recorder.AudioRecorder`
(``is_active`` / ``start`` / ``stop`` / ``cancel`` /
``set_completion_callback``), so :func:`speech_to_text.hotkeys.build_listener`
can drive it unchanged.

Unlike the dictation recorder it buffers raw int16 frames in RAM (no temp
WAV file) and, on stop, hands the completion callback a :class:`Clip` that
has already been silence-trimmed and measured. A watchdog thread enforces
the max-duration cap by auto-sending (calling ``stop()``) when exceeded.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import sounddevice as sd


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Clip:
    """A finished command-mode recording, trimmed and measured."""

    pcm: bytes
    duration_ms: int
    rms_dbfs: float
    loud_ms: int  # total ms of audio above the RMS threshold (length-invariant)
    stopped_at: float  # time.perf_counter() at release; latency anchor


def window_dbfs(frames: np.ndarray, sample_rate: int) -> np.ndarray:
    """Per-window dBFS levels for fixed 10 ms windows (``-120`` when silent)."""
    window = max(1, sample_rate // 100)
    n_windows = len(frames) // window
    if n_windows == 0:
        return np.empty(0)
    windows = frames[: n_windows * window].reshape(n_windows, window)
    rms = np.sqrt(np.mean(windows.astype(np.float64) ** 2, axis=1))
    dbfs = np.full(n_windows, -120.0)
    audible = rms > 0
    dbfs[audible] = 20.0 * np.log10(rms[audible] / 32768.0)
    return dbfs


def loud_speech_ms(
    frames: np.ndarray,
    sample_rate: int,
    threshold_dbfs: float,
) -> int:
    """Total milliseconds of audio whose 10 ms windows exceed ``threshold_dbfs``.

    Unlike a ratio over the whole clip this is invariant to how much silence
    the toggle-mode user leaves before/after speaking: surrounding quiet adds
    no loud windows. A single background-noise spike contributes only a few
    milliseconds, real speech hundreds.
    """
    dbfs = window_dbfs(frames, sample_rate)
    return round(float(np.count_nonzero(dbfs > threshold_dbfs))
                 * (sample_rate // 100) / sample_rate * 1000)


def overall_rms_dbfs(frames: np.ndarray) -> float:
    """Overall RMS level of int16 frames in dBFS (``-120`` for silence)."""
    if frames.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(frames.astype(np.float64) ** 2)))
    if rms <= 0:
        return -120.0
    return 20.0 * np.log10(rms / 32768.0)


def trim_silence(
    frames: np.ndarray,
    sample_rate: int,
    threshold_dbfs: float,
    pad_ms: int,
) -> np.ndarray:
    """Cut leading/trailing windows below ``threshold_dbfs``.

    Uses fixed 10 ms energy windows with a small pad so plosives survive.
    Returns an empty array when no window exceeds the threshold.
    """
    dbfs = window_dbfs(frames, sample_rate)
    loud = np.flatnonzero(dbfs > threshold_dbfs)
    if loud.size == 0:
        return np.empty(0, dtype=np.int16)

    window = max(1, sample_rate // 100)
    pad = int(sample_rate * pad_ms / 1000)
    start = max(0, int(loud[0]) * window - pad)
    end = min(len(frames), (int(loud[-1]) + 1) * window + pad)
    return frames[start:end]


class PcmRecorder:
    """Microphone capture for one command-mode session at a time."""

    def __init__(self, audio_cfg, command_cfg) -> None:  # noqa: ANN001 - typed configs
        self._audio_cfg = audio_cfg
        self._command_cfg = command_cfg
        self._is_recording = False
        self._stream: sd.InputStream | None = None
        self._drain_thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._queue: queue.Queue = queue.Queue()
        self._chunks: list[np.ndarray] = []
        self._on_complete: Callable[[Clip], None] | None = None

    @property
    def is_active(self) -> bool:
        """``True`` while a recording session is in progress."""
        return self._is_recording

    def set_completion_callback(self, on_complete: Callable[[Clip], None]) -> None:
        """Install the callback invoked with the finished :class:`Clip`."""
        self._on_complete = on_complete

    def start(self, on_complete: Callable[[Clip], None] | None = None) -> None:
        """Begin buffering microphone input. No-op while already active."""
        if self._is_recording:
            return

        if on_complete is not None:
            self._on_complete = on_complete
        if self._on_complete is None:
            raise RuntimeError("Ingen fullførings-callback satt.")

        self._is_recording = True
        self._chunks = []
        self._queue = queue.Queue()

        self._stream = sd.InputStream(
            samplerate=self._audio_cfg.sample_rate,
            channels=self._audio_cfg.channels,
            dtype="int16",
            callback=self._put_audio,
        )
        self._stream.start()

        self._drain_thread = threading.Thread(target=self._drain, daemon=True)
        self._drain_thread.start()
        self._watchdog_thread = threading.Thread(target=self._watchdog, daemon=True)
        self._watchdog_thread.start()

    def stop(self) -> None:
        """Finish the session and deliver the trimmed clip to the callback."""
        if not self._is_recording:
            return
        self._is_recording = False
        stopped_at = time.perf_counter()

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        # Vekk draineren umiddelbart – ellers venter join() opptil 100 ms på
        # at get(timeout=0.1) går ut på tid, og den ventetiden havner midt i
        # latensbudsjettet mellom slipp og sending.
        self._queue.put(None)

        # The watchdog may be the caller; never join ourselves.
        if self._watchdog_thread is not None and self._watchdog_thread is not threading.current_thread():
            self._watchdog_thread.join()
        self._watchdog_thread = None
        if self._drain_thread is not None and self._drain_thread is not threading.current_thread():
            self._drain_thread.join()
        self._drain_thread = None

        clip = self._build_clip(stopped_at)
        callback = self._on_complete
        if callback is not None:
            threading.Thread(target=callback, args=(clip,), daemon=True).start()

    def cancel(self) -> None:
        """Abort the current session without invoking the callback."""
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

        self._queue.put(None)  # vekk draineren (samme grunn som i stop())

        if self._watchdog_thread is not None and self._watchdog_thread is not threading.current_thread():
            self._watchdog_thread.join()
        self._watchdog_thread = None
        if self._drain_thread is not None and self._drain_thread is not threading.current_thread():
            self._drain_thread.join()
        self._drain_thread = None

        self._chunks = []

    def _put_audio(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            logger.debug("Audio callback status: %s", status)
        self._queue.put(indata.copy())

    def _drain(self) -> None:
        while self._is_recording or not self._queue.empty():
            try:
                chunk = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:  # sentinel fra stop()/cancel(): strømmen er lukket
                break
            self._chunks.append(chunk)

    def _watchdog(self) -> None:
        deadline = time.monotonic() + self._command_cfg.max_duration_ms / 1000
        while self._is_recording and time.monotonic() < deadline:
            time.sleep(0.05)
        if self._is_recording:
            logger.info(
                "Opptakstak (%d ms) nådd – sender automatisk.",
                self._command_cfg.max_duration_ms,
            )
            self.stop()

    def _build_clip(self, stopped_at: float) -> Clip:
        if not self._chunks:
            return Clip(pcm=b"", duration_ms=0, rms_dbfs=-120.0, loud_ms=0,
                        stopped_at=stopped_at)

        frames = np.concatenate(self._chunks).flatten()
        self._chunks = []
        full_duration_ms = round(len(frames) / self._audio_cfg.sample_rate * 1000)

        loud_ms = loud_speech_ms(
            frames,
            self._audio_cfg.sample_rate,
            self._command_cfg.rms_threshold_dbfs,
        )
        trimmed = trim_silence(
            frames,
            self._audio_cfg.sample_rate,
            self._command_cfg.rms_threshold_dbfs,
            self._command_cfg.silence_pad_ms,
        )
        if trimmed.size == 0 or loud_ms < self._command_cfg.min_loud_ms:
            logger.info(
                "RMS-gate: forkaster opptak (%d ms klipp, tale %d ms < %d ms).",
                full_duration_ms, loud_ms, self._command_cfg.min_loud_ms,
            )
            return Clip(pcm=b"", duration_ms=full_duration_ms, rms_dbfs=-120.0,
                        loud_ms=loud_ms, stopped_at=stopped_at)

        return Clip(
            pcm=trimmed.tobytes(),
            duration_ms=round(len(trimmed) / self._audio_cfg.sample_rate * 1000),
            rms_dbfs=round(overall_rms_dbfs(trimmed), 1),
            loud_ms=loud_ms,
            stopped_at=stopped_at,
        )
