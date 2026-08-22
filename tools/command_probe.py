#!/usr/bin/env python3
"""Send en WAV-fil mot kommandotjenesten og sjekk svaret (TC-regresjon).

Konverterer en WAV-fil til rå 16-bit PCM-bytes, poster den til /command og
printer konvolutten pent. Med ``--expect`` settes exit-kode 0/1 etter om
matchet regel stemmer – dermed kan TC-01–TC-03 skriptes.

Bruk::

    python tools/command_probe.py klipp.wav
    python tools/command_probe.py copy_that.wav --expect copy_that --url http://192.168.1.200:8002/command

Krever bare stdlib + requests.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path


DEFAULT_URL = "http://192.168.1.200:8002/command"


def wav_to_pcm16(path: Path) -> tuple[bytes, int]:
    """Return raw mono s16le bytes and the file's sample rate."""
    with wave.open(str(path), "rb") as wav:
        if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2:
            raise ValueError(f"{path.name}: forventet 16-bit PCM")
        rate = wav.getframerate()
        pcm = wav.readframes(wav.getnframes())
    return pcm, rate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wav", type=Path, help="WAV-fil med kommandoen")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--expect", help="Regel som må matches (exit 1 hvis ikke)")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    try:
        pcm, rate = wav_to_pcm16(args.wav)
    except Exception as e:
        print(f"FEIL: {e}", file=sys.stderr)
        return 2

    duration_ms = round(len(pcm) / 2 / rate * 1000)
    print(f"Sender {args.wav.name} ({duration_ms} ms @ {rate} Hz) -> {args.url}")

    t0 = time.perf_counter()
    try:
        response = __import__("requests").post(
            args.url,
            data=pcm,
            headers={"Content-Type": "application/octet-stream"},
            timeout=args.timeout,
        )
    except Exception as e:
        print(f"FEIL: kommandotjenesten svarer ikke: {e}", file=sys.stderr)
        return 2
    client_rtt_ms = round((time.perf_counter() - t0) * 1000, 1)

    envelope = response.json()
    print(json.dumps(envelope, indent=2, ensure_ascii=False))
    print(f"klient-RTT: {client_rtt_ms} ms")

    if args.expect is None:
        return 0

    matched = envelope.get("rule")
    ok = envelope.get("status") == "ok" and matched == args.expect
    print(f"{'PASS' if ok else 'FAIL'}: forventet {args.expect!r}, fikk {matched!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
