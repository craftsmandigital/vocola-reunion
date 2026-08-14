"""Speech-to-text dictation client.

A lightweight global-hotkey dictation tool that records microphone audio,
sends it to a local faster-whisper server for transcription, and pastes
the result at the cursor.
"""

from __future__ import annotations

from .app import main

__all__ = ["main"]