"""Transcription HTTP client.

Posts the recorded WAV to the local faster-whisper server (an OpenAI-
compatible ``/v1/audio/transcriptions`` endpoint) and returns the
recognised text. Network errors and non-2xx responses are reported via
logging and returned as ``None`` so the caller can decide what to do.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

from .config import ServerConfig


logger = logging.getLogger(__name__)


def transcribe_file(path: str | os.PathLike[str], server: ServerConfig) -> str | None:
    """Send ``path`` to the whisper server and return the recognised text.

    Returns ``None`` on any failure (network error, non-200 status, empty
    payload). Error messages are printed in Norwegian to match the rest of
    the app's output.
    """
    file_path = Path(path)
    try:
        with file_path.open("rb") as f:
            files = {"file": (file_path.name, f, "audio/wav")}
            data: dict[str, str] = {"model": server.model}
            if server.language:
                data["language"] = server.language
            response = requests.post(
                server.url,
                files=files,
                data=data,
                timeout=server.timeout,
            )
    except Exception as e:
        logger.error("Det oppstod en feil under transkribering: %s", e)
        return None

    if response.status_code != 200:
        logger.error("Feil fra serveren (%s): %s", response.status_code, response.text)
        return None

    text = (response.json().get("text") or "").strip()
    if not text:
        logger.warning("Serveren hørte ingen tale i opptaket.")
        return None

    return text