"""Configuration loading and typed config objects.

Loads ``config.yaml`` from the project root (the directory containing
``main.py``) and exposes the merged values as frozen dataclasses so the rest
of the package can rely on attribute access instead of nested dict lookups.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml


logger = logging.getLogger(__name__)


DEFAULT_CONFIG: dict[str, dict[str, object]] = {
    "server": {
        "url": "http://192.168.1.200:8000/v1/audio/transcriptions",
        "model": "whisper-1",
        "language": "",
        "timeout": 30,
    },
    "shortcuts": {
        "toggle": "<ctrl>+<shift>+i",
        "cancel": "<esc>",
    },
    "audio": {
        "sample_rate": 16000,
        "channels": 1,
    },
    "paste": {
        "method": "ctrl_v",
        "paste_delay": 0.05,
        "exclude_from_history": True,
        "restore_clipboard": True,
        "restore_delay": 0.15,
        "fallback_to_direct": True,
    },
    "ui": {
        "title_prefix": "Whisper-to-Text",
        "title_ready": "🔵 [Ready]",
        "title_recording": "🔴 [Recording...]",
        "title_processing": "⏳ [Processing...]",
        "overlay_recording": "🎤  Recording...",
        "overlay_processing": "⏳  Processing...",
        "overlay_done": "✅  Pasted!",
        "overlay_cancelled": "❌  Cancelled",
        "overlay_error": "⚠️  Error",
        "overlay_opacity": 0.88,
        "overlay_auto_hide_seconds": 2.0,
    },
    "logging": {
        "console_level": "INFO",
        "file_level": "DEBUG",
        "file_name": "speech-to-text.log",
    },
}


@dataclass(frozen=True)
class ServerConfig:
    """Settings for the local faster-whisper server."""

    url: str
    model: str
    language: str
    timeout: int


@dataclass(frozen=True)
class ShortcutConfig:
    """Pynput-style shortcut strings for the global hotkeys."""

    toggle: str
    cancel: str


@dataclass(frozen=True)
class AudioConfig:
    """Microphone capture settings."""

    sample_rate: int
    channels: int


@dataclass(frozen=True)
class PasteConfig:
    """How transcribed text is delivered to the active application."""

    method: str
    paste_delay: float
    exclude_from_history: bool
    restore_clipboard: bool
    restore_delay: float
    fallback_to_direct: bool = True


@dataclass(frozen=True)
class UiConfig:
    """Terminal title fragments shown while idle/recording/processing."""

    title_prefix: str
    title_ready: str
    title_recording: str
    title_processing: str
    overlay_recording: str = "🎤  Recording..."
    overlay_processing: str = "⏳  Processing..."
    overlay_done: str = "✅  Pasted!"
    overlay_cancelled: str = "❌  Cancelled"
    overlay_error: str = "⚠️  Error"
    overlay_opacity: float = 0.88
    overlay_auto_hide_seconds: float = 2.0


@dataclass(frozen=True)
class LoggingConfig:
    """Logging configuration."""

    console_level: str
    file_level: str
    file_name: str


@dataclass(frozen=True)
class AppConfig:
    """Fully merged, typed application configuration."""

    server: ServerConfig
    shortcuts: ShortcutConfig
    audio: AudioConfig
    paste: PasteConfig
    ui: UiConfig
    logging: LoggingConfig


def _project_root() -> Path:
    """Return the project root directory (where ``main.py`` lives).

    ``config.py`` is one level deeper under ``src/speech_to_text/``, so we
    walk up two directories to find the sibling ``config.yaml``.
    """
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    """Return the default ``config.yaml`` path next to ``main.py``."""
    return _project_root() / "config.yaml"


def load_config(path: Path | str | None = None) -> AppConfig:
    """Read YAML config from ``path`` and merge with ``DEFAULT_CONFIG``.

    Falls back to built-in defaults if the file is missing or unparseable.
    Each top-level section is merged shallowly: user keys override defaults,
    unknown keys are ignored, and missing sections use defaults as-is.
    """
    config_path = Path(path) if path is not None else default_config_path()

    if not config_path.exists():
        logger.warning("Fant ikke '%s'. Bruker innebygde standardverdier.", config_path)
        return _build_app_config(DEFAULT_CONFIG)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logger.warning("Kunne ikke tolke '%s': %s. Bruker innebygde standardverdier.", config_path, e)
        return _build_app_config(DEFAULT_CONFIG)

    merged: dict[str, dict[str, object]] = {}
    for section, defaults in DEFAULT_CONFIG.items():
        user_section = user_config.get(section, {}) or {}
        merged[section] = {**defaults, **user_section}
    return _build_app_config(merged)


def _build_app_config(merged: dict[str, dict[str, object]]) -> AppConfig:
    server = merged["server"]
    shortcuts = merged["shortcuts"]
    audio = merged["audio"]
    paste = merged["paste"]
    ui = merged["ui"]
    logging_cfg = merged.get("logging", {})

    return AppConfig(
        server=ServerConfig(
            url=str(server["url"]),
            model=str(server["model"]),
            language=str(server["language"]),
            timeout=int(server["timeout"]),
        ),
        shortcuts=ShortcutConfig(
            toggle=str(shortcuts["toggle"]),
            cancel=str(shortcuts["cancel"]),
        ),
        audio=AudioConfig(
            sample_rate=int(audio["sample_rate"]),
            channels=int(audio["channels"]),
        ),
        paste=PasteConfig(
            method=str(paste["method"]),
            paste_delay=float(paste["paste_delay"]),
            exclude_from_history=bool(paste["exclude_from_history"]),
            restore_clipboard=bool(paste["restore_clipboard"]),
            restore_delay=float(paste["restore_delay"]),
            fallback_to_direct=bool(paste.get("fallback_to_direct", True)),
        ),
        ui=UiConfig(
            title_prefix=str(ui["title_prefix"]),
            title_ready=str(ui["title_ready"]),
            title_recording=str(ui["title_recording"]),
            title_processing=str(ui["title_processing"]),
            overlay_recording=str(ui.get("overlay_recording", "🎤  Recording...")),
            overlay_processing=str(ui.get("overlay_processing", "⏳  Processing...")),
            overlay_done=str(ui.get("overlay_done", "✅  Pasted!")),
            overlay_cancelled=str(ui.get("overlay_cancelled", "❌  Cancelled")),
            overlay_error=str(ui.get("overlay_error", "⚠️  Error")),
            overlay_opacity=float(ui.get("overlay_opacity", 0.88)),
            overlay_auto_hide_seconds=float(ui.get("overlay_auto_hide_seconds", 2.0)),
        ),
        logging=LoggingConfig(
            console_level=str(logging_cfg.get("console_level", "INFO")),
            file_level=str(logging_cfg.get("file_level", "DEBUG")),
            file_name=str(logging_cfg.get("file_name", "speech-to-text.log")),
        ),
    )