"""Kommandomodus-klient.

Toggle-opptak (samme tast starter/stopper), energi-trimming og RMS-gate i
:class:`PcmRecorder`, rå PCM til kommandotjenesten, og utførelse av
handlingssekvensen fra konvolutten på klient-OS.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import replace

import requests
from pynput import keyboard

from .config import default_config_path, load_config
from .hotkeys import build_listener
from .pcm_recorder import Clip, PcmRecorder
from .platform_info import IS_WINDOWS
from .shortcuts import parse_shortcut
from .ui import (
    StatusOverlay,
    get_foreground_window,
    set_foreground_window,
    setup_logging,
)


logger = logging.getLogger(__name__)

# Ønsket: connection reuse over TCP (sparer ~1-3 ms per request på LAN).
_session = requests.Session()

# Ønsket: gjenbruk pynput-controller (init kan kreve noen ms ved hver instansiering).
_controller = keyboard.Controller()


# pynput-navn som avviker fra nøkkelnavnene i grammatikken.
_KEY_ALIASES: dict[str, str] = {
    "escape": "esc",
    "return": "enter",
    "control": "ctrl",
    "cmd": "cmd",
    "super": "cmd",
    "win": "cmd",
}

_SPECIAL_KEYS: frozenset[str] = frozenset({
    "ctrl", "alt", "alt_gr", "shift", "cmd",
    "enter", "esc", "tab", "space", "backspace", "delete", "insert",
    "home", "end", "page_up", "page_down",
    "up", "down", "left", "right",
}) | {f"f{n}" for n in range(1, 25)}


class _FocusRecorder(PcmRecorder):
    """Captures the foreground window when recording starts."""

    def __init__(self, audio_cfg, command_cfg, overlay: StatusOverlay | None = None) -> None:  # noqa: ANN001
        super().__init__(audio_cfg, command_cfg)
        self.target_hwnd: int | None = None
        self._overlay = overlay

    def start(self, on_complete=None) -> None:  # type: ignore[override]
        self.target_hwnd = get_foreground_window()
        if self._overlay is not None:
            self._overlay.show_recording()
        super().start(on_complete)

    def stop(self) -> None:
        if self._overlay is not None:
            self._overlay.show_processing()
        super().stop()

    def cancel(self) -> None:
        if self._overlay is not None:
            self._overlay.show_cancelled()
        super().cancel()


def _resolve_key(name: str) -> keyboard.Key | keyboard.KeyCode | None:
    """Map a grammar key name to a pynput key; ``None`` if unknown."""
    normalized = _KEY_ALIASES.get(name.lower(), name.lower())
    if normalized in _SPECIAL_KEYS:
        return getattr(keyboard.Key, normalized, None)
    if len(normalized) == 1:
        return keyboard.KeyCode.from_char(normalized)
    logger.warning("Ukjent tast i handlingssekvens: %r", name)
    return None


def execute_actions(actions: list[dict], anchor: float) -> float | None:
    """Run an action sequence; return ms from ``anchor`` to first keypress."""
    first_keypress_ms: float | None = None

    for operation in actions:
        kind = operation.get("action")
        if kind == "keypress":
            keys = [key for key in (_resolve_key(k) for k in operation.get("keys", []))
                    if key is not None]
            if not keys:
                continue
            for key in keys:
                _controller.press(key)
            for key in reversed(keys):
                _controller.release(key)
            if first_keypress_ms is None:
                first_keypress_ms = round((time.perf_counter() - anchor) * 1000, 1)
        elif kind == "wait":
            time.sleep(operation.get("ms", 0) / 1000)
        elif kind == "type_text":
            _controller.type(operation.get("text", ""))
        else:
            logger.warning("Ukjent handlingstype: %r", kind)

    return first_keypress_ms


def main() -> None:
    """Load config, register toggle/cancel, run until Ctrl+C."""
    cfg = load_config()
    cmd = cfg.command
    config_path = default_config_path()

    print("==================================================")
    print("⚡ Kommandomodus startet!")
    print("==================================================")
    print(f"Konfigurasjon lastet fra: {config_path}")
    print(f"Kommandotjeneste: {cmd.url}")
    print("\nSnarveier registrert:")
    print(f"  - {cmd.toggle}  (start/stopp kommandoopptak)")
    print(f"  - {cmd.cancel}  (avbryt pågående opptak)")
    print(f"\nGater: min {cmd.min_duration_ms} ms, tak {cmd.max_duration_ms} ms, "
          f"RMS > {cmd.rms_threshold_dbfs} dBFS, tale >= {cmd.min_loud_ms} ms")
    print("(Trykk Ctrl + C i denne terminalen for å avslutte appen)")
    print("--------------------------------------------------")

    setup_logging(cfg)

    ui_queue: queue.Queue[callable] = queue.Queue()
    ui_cfg = replace(cfg.ui, overlay_done=cfg.ui.command_overlay_done)
    overlay = StatusOverlay(ui_cfg, ui_queue)

    recorder = _FocusRecorder(cfg.audio, cmd, overlay)

    def _on_clip(clip: Clip) -> None:
        threading.Thread(target=_worker, args=(recorder, clip, cmd, overlay),
                         daemon=True).start()

    toggle = parse_shortcut(cmd.toggle)
    cancel = parse_shortcut(cmd.cancel)
    listener = build_listener(recorder, toggle, cancel, _on_clip)
    listener.start()

    _shutdown = threading.Event()

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

    try:
        while not _shutdown.is_set():
            overlay.flush()
            overlay.root.update()
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


def _worker(recorder: _FocusRecorder, clip: Clip, cmd, overlay: StatusOverlay) -> None:  # noqa: ANN001
    """Gate, send, parse and execute – runs off the UI thread."""
    try:
        if not clip.pcm:
            overlay.show_error("Ingen tale gjenkjent")
            return
        if clip.duration_ms < cmd.min_duration_ms:
            overlay.show_error(f"For kort opptak ({clip.duration_ms} ms)")
            return

        logger.info(
            "Sender %d ms klipp (RMS %.1f dBFS, tale %d ms) ...",
            clip.duration_ms, clip.rms_dbfs, clip.loud_ms,
        )
        # Start fokusgjenoppretning i bakgrunnen mens serveren prosesserer
        # (30-40 ms). Slik er fokus klart før handlinger skal sendes.
        focus_thread: threading.Thread | None = None
        if recorder.target_hwnd:
            focus_thread = threading.Thread(
                target=set_foreground_window, args=(recorder.target_hwnd,),
                daemon=True,
            )
            focus_thread.start()
        response = _session.post(
            cmd.url,
            data=clip.pcm,
            headers={"Content-Type": "application/octet-stream"},
            timeout=cmd.timeout_seconds,
        )
        envelope = response.json()
    except Exception as e:
        logger.error("Kommandotjenesten svarte ikke: %s", e)
        overlay.show_error(f"Serverfeil: {str(e)[:40]}")
        return

    status = envelope.get("status")
    heard = envelope.get("heard", "")
    logger.info(
        "Konvolutt: status=%s hørt=%r rule=%s timing=%s",
        status, heard, envelope.get("rule"), envelope.get("timing_ms"),
    )

    if status == "error":
        detail = envelope.get("error") or ""
        if detail == "no speech recognized":
            overlay.show_error("Ingen tale gjenkjent")
        else:
            overlay.show_error(f"Serverfeil: {detail[:30]}" if detail else "Serverfeil")
        return
    if status != "ok":
        label = heard[:30] if heard else "ingen tale"
        overlay.show_error(f"Ukjent kommando: {label}")
        return

    if focus_thread is not None:
        focus_thread.join(timeout=0.1)  # sikker: fokus er nesten alltid ferdig nå

    first_keypress_ms = execute_actions(envelope.get("actions", []), anchor=clip.stopped_at)
    if first_keypress_ms is not None:
        logger.info(
            "Ende-til-ende latens (slipp → første tastetrykk): %.1f ms "
            "(server: %s)",
            first_keypress_ms, envelope.get("timing_ms"),
        )
    overlay.show_done(str(envelope.get("rule") or ""))


if __name__ == "__main__":
    main()
