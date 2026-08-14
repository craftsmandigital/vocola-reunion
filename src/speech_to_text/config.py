"""Configuration loading and typed config objects.

Loads ``config.yaml`` from the project root (the directory containing
``main.py``) and exposes the merged values as frozen dataclasses so the rest
of the package can rely on attribute access instead of nested dict lookups.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml


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
    "behavior": {
        "toggle_debounce_seconds": 0.4,
        "main_loop_sleep_seconds": 0.5,
    },
    "paste": {
        "method": "ctrl_v",
        "paste_delay": 0.05,
        "exclude_from_history": True,
        "restore_clipboard": True,
        "restore_delay": 0.15,
    },
    "ui": {
        "title_prefix": "Whisper-to-Text",
        "title_ready": "🔵 [KLAR]",
        "title_recording": "🔴 [OPPTAK...]",
        "title_processing": "⏳ [BEHANDLER...]",
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
class BehaviorConfig:
    """Timing tweaks for hotkey debouncing and the main loop."""

    toggle_debounce_seconds: float
    main_loop_sleep_seconds: float


@dataclass(frozen=True)
class PasteConfig:
    """How transcribed text is delivered to the active application."""

    method: str
    paste_delay: float
    exclude_from_history: bool
    restore_clipboard: bool
    restore_delay: float


@dataclass(frozen=True)
class UiConfig:
    """Terminal title fragments shown while idle/recording/processing."""

    title_prefix: str
    title_ready: str
    title_recording: str
    title_processing: str


@dataclass(frozen=True)
class AppConfig:
    """Fully merged, typed application configuration."""

    server: ServerConfig
    shortcuts: ShortcutConfig
    audio: AudioConfig
    behavior: BehaviorConfig
    paste: PasteConfig
    ui: UiConfig


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
        print(f"⚠️  Fant ikke '{config_path}'. Bruker innebygde standardverdier.")
        return _build_app_config(DEFAULT_CONFIG)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        print(f"⚠️  Kunne ikke tolke '{config_path}': {e}. Bruker innebygde standardverdier.")
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
    behavior = merged["behavior"]
    paste = merged["paste"]
    ui = merged["ui"]

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
        behavior=BehaviorConfig(
            toggle_debounce_seconds=float(behavior["toggle_debounce_seconds"]),
            main_loop_sleep_seconds=float(behavior["main_loop_sleep_seconds"]),
        ),
        paste=PasteConfig(
            method=str(paste["method"]),
            paste_delay=float(paste["paste_delay"]),
            exclude_from_history=bool(paste["exclude_from_history"]),
            restore_clipboard=bool(paste["restore_clipboard"]),
            restore_delay=float(paste["restore_delay"]),
        ),
        ui=UiConfig(
            title_prefix=str(ui["title_prefix"]),
            title_ready=str(ui["title_ready"]),
            title_recording=str(ui["title_recording"]),
            title_processing=str(ui["title_processing"]),
        ),
    )