"""Unit tests for PcmRecorder's trimming and loud-speech gate.

Loads ``src/speech_to_text/pcm_recorder.py`` standalone (no package import,
no audio hardware): ``sounddevice`` is stubbed when missing so the module
imports cleanly, and clips are injected straight into ``_build_clip``.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pytest

try:  # bare tilgjengelig i et fullt miljø; stubbes ellers
    import sounddevice  # noqa: F401
except ImportError:
    _stub = types.ModuleType("sounddevice")
    _stub.InputStream = object
    sys.modules.setdefault("sounddevice", _stub)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PCM_PATH = _REPO_ROOT / "src" / "speech_to_text" / "pcm_recorder.py"

_spec = importlib.util.spec_from_file_location("pcm_recorder_test", _PCM_PATH)
assert _spec is not None and _spec.loader is not None
pcm = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pcm  # kreves for dataclasses med string-annotasjoner
_spec.loader.exec_module(pcm)

SR = 16000


class _AudioCfg:
    sample_rate = SR
    channels = 1


def _command_cfg(min_loud_ms: int = 60, threshold: float = -45.0):
    class _CmdCfg:
        pass

    cfg = _CmdCfg()
    cfg.rms_threshold_dbfs = threshold
    cfg.silence_pad_ms = 60
    cfg.min_loud_ms = min_loud_ms
    cfg.max_duration_ms = 8000
    cfg.min_duration_ms = 200
    return cfg


def make_recorder(**kwargs) -> "pcm.PcmRecorder":
    return pcm.PcmRecorder(_AudioCfg(), _command_cfg(**kwargs))


def build_clip(rec: "pcm.PcmRecorder", frames: np.ndarray) -> "pcm.Clip":
    rec._chunks = [frames]
    return rec._build_clip(0.0)


def noise(seed: int, sigma: float, n_samples: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, sigma, n_samples).astype(np.int16)


def dbfs_of(sigma: float) -> float:
    return 20.0 * np.log10(sigma / 32768.0)


def test_pure_silence_is_rejected() -> None:
    clip = build_clip(make_recorder(), noise(1, 30, SR * 2))
    assert clip.pcm == b""
    assert clip.loud_ms == 0


def test_single_spike_in_silence_is_rejected() -> None:
    silence = noise(2, 30, SR * 2)
    spike = noise(3, 4000, 200)  # ~12 ms burst, ~-18 dBFS
    clip = build_clip(make_recorder(),
                      np.concatenate([silence[:SR], spike, silence[SR:]]))
    assert clip.pcm == b""
    assert clip.loud_ms < 60


def test_sparse_speech_in_long_clip_is_accepted() -> None:
    """Regression: ekte tale omgitt av lang toggle-stillhet skal slippe gjennom."""
    silence = noise(4, 40, SR * 2)
    speech = noise(5, 2500, SR // 2)  # 500 ms ved ~-23 dBFS
    clip = build_clip(make_recorder(),
                      np.concatenate([silence[:SR], speech, silence[SR - len(speech):]]))
    assert clip.pcm != b""
    assert clip.loud_ms >= 400
    assert clip.duration_ms >= 500  # trim beholder hele tale-spennet + pad


def test_gate_respects_min_loud_ms() -> None:
    short_speech = noise(6, 2500, SR // 8)  # 125 ms tale, under gulvet på 200 ms
    clip = build_clip(make_recorder(min_loud_ms=200), short_speech)
    assert clip.pcm == b""


def test_continuous_noise_is_accepted() -> None:
    clip = build_clip(make_recorder(), noise(7, 2500, SR))
    assert clip.pcm != b""
    assert clip.loud_ms >= 900


def test_loud_speech_ms_full_second() -> None:
    frames = noise(8, 2500, SR)  # alle 100 vinduene over terskel
    assert pcm.loud_speech_ms(frames, SR, -45.0) == pytest.approx(1000, abs=1)
