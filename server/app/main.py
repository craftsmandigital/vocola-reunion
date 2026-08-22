"""Kommandotjenesten: mottar rå PCM, transkriberer, parser, returnerer konvolutt.

Én FastAPI-tjeneste som i samme prosess kjører faster-whisper (lettvekts-
modell) og grammatikk-parseren. Klienten poster rå 16 kHz mono 16-bit PCM;
svaret er alltid en konvolutt::

    {"status": "ok|unknown|error",
     "heard": "...",
     "rule": "...|null",
     "actions": [...],
     "timing_ms": {"total", "asr", "parse"}}
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


logger = logging.getLogger("command_service")

MODEL_NAME = os.environ.get("COMMAND_MODEL", "base.en")
DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8_float16")
GRAMMAR_PATH = os.environ.get("GRAMMAR_PATH", "/etc/command-grammar/rules.yaml")

# Deterministisk dekoding per krav FK-2.3.
TRANSCRIBE_OPTIONS = {
    "beam_size": 1,
    "temperature": 0.0,
    "language": "en",
    "condition_on_previous_text": False,
    "vad_filter": False,
    "without_timestamps": True,
}

_state: dict[str, Any] = {}


def _build_initial_prompt(grammar: Any) -> str:
    """Bias modellen mot gyldig kommandovokabular (krav FK-2.2)."""
    phrases = grammar.phrases()
    if not phrases:
        return ""
    return ", ".join(phrases)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from faster_whisper import WhisperModel

    from .parser import Grammar

    logging.basicConfig(level=logging.INFO)
    logger.info(
        "Laster modell %s (device=%s, compute_type=%s) ...",
        MODEL_NAME, DEVICE, COMPUTE_TYPE,
    )
    _state["model"] = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
    _state["grammar"] = Grammar.from_yaml(GRAMMAR_PATH)
    _state["initial_prompt"] = _build_initial_prompt(_state["grammar"])
    logger.info(
        "Grammatikk lastet: %d fraser. initial_prompt: %r",
        len(_state["grammar"]), _state["initial_prompt"],
    )
    yield
    _state.clear()


app = FastAPI(title="command-service", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "rules": len(_state.get("grammar", [])),
        "ready": "model" in _state,
    }


@app.post("/command")
async def command(request: Request) -> JSONResponse:
    """Transkriber rå PCM og returner konvolutten."""
    t_start = time.perf_counter()
    body = await request.body()

    try:
        audio = np.frombuffer(body, dtype=np.int16).astype(np.float32) / 32768.0
    except ValueError:
        return JSONResponse(_envelope(status="error", heard="", timing=t_start),
                            status_code=400)

    if audio.size == 0:
        envelope = _envelope(status="error", heard="", timing=t_start)
        envelope["error"] = "empty audio"
        return JSONResponse(envelope)

    model = _state["model"]
    t_asr0 = time.perf_counter()
    segments, _info = model.transcribe(
        audio,
        initial_prompt=_state["initial_prompt"] or None,
        **TRANSCRIBE_OPTIONS,
    )
    # Segment-generatoren er lat: iterér ferdig før ASR-tiden måles.
    text = " ".join(segment.text for segment in segments).strip()
    t_asr1 = time.perf_counter()

    match = _state["grammar"].match(text) if text else None
    t_parse = time.perf_counter()

    if match is None:
        envelope = _envelope(status="unknown" if text else "error", heard=text,
                             timing=t_start, asr=(t_asr0, t_asr1), parse=(t_asr1, t_parse))
        if not text:
            envelope["error"] = "no speech recognized"
        logger.info("Ukjent kommando (hørt: %r)", text)
        return JSONResponse(envelope)

    envelope = _envelope(status="ok", heard=text, rule=match.name,
                         actions=list(match.actions), timing=t_start,
                         asr=(t_asr0, t_asr1), parse=(t_asr1, t_parse))
    logger.info("Match: %r -> %s (%s)", text, match.name, envelope["timing_ms"])
    return JSONResponse(envelope)


def _envelope(
    status: str,
    heard: str,
    timing: float,
    asr: tuple[float, float] | None = None,
    parse: tuple[float, float] | None = None,
    rule: str | None = None,
    actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    total_ms = round((time.perf_counter() - timing) * 1000, 2)
    timing_ms: dict[str, float] = {"total": total_ms}
    if asr is not None:
        timing_ms["asr"] = round((asr[1] - asr[0]) * 1000, 2)
    if parse is not None:
        timing_ms["parse"] = round(round((parse[1] - parse[0]) * 1000, 4), 4)
    return {
        "status": status,
        "heard": heard,
        "rule": rule,
        "actions": actions or [],
        "timing_ms": timing_ms,
    }
