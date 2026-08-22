#!/usr/bin/env python3
"""Latensrapport for kommandomodus fra speech-to-text.log.

Parser logglinjene kommandoklienten skriver (klipp-størrelse/RMS, konvolutt,
ende-til-ende-latens) og summerer:

  * antall sendinger / matcher / ukjente / gate-forkastelser
  * ende-til-ende-latens: min/median/p95/maks mot NFK-1.1-målet (<100 ms)
  * server-timing per etappe (asr/parse/total) mot NFK-1.2/NFK-1.3
  * RMS- og varighetsfordeling på godkjente klipp – grunnlag for å justere
    ``rms_threshold_dbfs`` og ``min_duration_ms``

Bruk::

    python tools/latency_report.py                     # repoets loggfil
    python tools/latency_report.py --log annen.log     # egen fil

Krever bare stdlib.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path


RE_SEND = re.compile(
    r"Sender (?P<ms>\d+) ms klipp \(RMS (?P<rms>-?[\d.]+) dBFS"
    r"(?:, taledel (?P<ratio>[\d.]+)%)?(?:, tale (?P<loud>\d+) ms)?\)")
RE_ENVELOPE = re.compile(
    r"Konvolutt: status=(?P<status>\w+) hørt=(?P<heard>'[^']*'|\"[^\"]*\") rule=(?P<rule>\S+) timing=(?P<timing>.+)$")
RE_LATENCY = re.compile(
    r"Ende-til-ende latens \(slipp → første tastetrykk\): (?P<ms>[\d.]+) ms")
RE_TIMING = re.compile(r"'asr': (?P<asr>-?[\d.]+).*'parse': (?P<parse>-?[\d.]+).*'total': (?P<total>-?[\d.]+)")


@dataclass
class Session:
    clip_ms: int | None = None
    rms_dbfs: float | None = None
    speech_ratio: float | None = None  # gammelt format (taledel %)
    loud_ms: int | None = None  # nytt format (absolutt ms tale)
    status: str | None = None
    rule: str | None = None
    heard: str = ""
    e2e_ms: float | None = None
    asr_ms: float | None = None
    parse_ms: float | None = None
    total_ms: float | None = None


def parse_log(path: Path) -> tuple[list[Session], int, int]:
    sessions: list[Session] = []
    gate_rejects = 0
    cap_sends = 0
    current: Session | None = None

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if m := RE_SEND.search(line):
            current = Session(clip_ms=int(m["ms"]), rms_dbfs=float(m["rms"]))
            if m["ratio"] is not None:
                current.speech_ratio = float(m["ratio"])
            if m["loud"] is not None:
                current.loud_ms = int(m["loud"])
            sessions.append(current)
            continue
        if "RMS-gate:" in line:
            gate_rejects += 1
            continue
        if "Opptakstak" in line and "sender automatisk" in line:
            cap_sends += 1
            continue
        if m := RE_ENVELOPE.search(line):
            if current is None:
                current = Session()
                sessions.append(current)
            current.status = m["status"]
            current.rule = None if m["rule"] == "None" else m["rule"].strip("'\"")
            try:
                current.heard = eval(m["heard"], {"__builtins__": {}})  # noqa: S307 - eget format
            except Exception:
                current.heard = m["heard"]
            if mt := RE_TIMING.search(m["timing"]):
                current.asr_ms = float(mt["asr"])
                current.parse_ms = float(mt["parse"])
                current.total_ms = float(mt["total"])
            continue
        if m := RE_LATENCY.search(line):
            target = current if current is not None else Session()
            if current is None:
                sessions.append(target)
            target.e2e_ms = float(m["ms"])

    return [s for s in sessions if s.status is not None], gate_rejects, cap_sends


def _stats(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95_index = max(0, round(0.95 * len(ordered)) - 1)
    return {
        "min": min(values),
        "median": statistics.median(values),
        "p95": ordered[p95_index],
        "maks": max(values),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    default_log = Path(__file__).resolve().parents[1] / "speech-to-text.log"
    parser.add_argument("--log", type=Path, default=default_log)
    args = parser.parse_args()

    if not args.log.exists():
        print(f"Finner ikke loggfilen: {args.log}", file=sys.stderr)
        return 2

    sessions, gate_rejects, cap_sends = parse_log(args.log)
    if not sessions:
        print("Ingen kommandosendinger i loggen ennå.")
        return 0

    matched = [s for s in sessions if s.status == "ok"]
    unknown = [s for s in sessions if s.status == "unknown"]
    errors = [s for s in sessions if s.status == "error"]

    print(f"=== Kommandomodus-rapport ({args.log.name}) ===")
    print(f"\nSendinger: {len(sessions)}  "
          f"(match: {len(matched)}, ukjent: {len(unknown)}, feil: {len(errors)})")
    print(f"Gate-forkastelser (stille): {gate_rejects}   Opptakstak utløst: {cap_sends}")

    if matched:
        rules: dict[str, int] = {}
        for s in matched:
            rules[s.rule or "?"] = rules.get(s.rule or "?", 0) + 1
        print("\nMatchede regler:")
        for rule, count in sorted(rules.items(), key=lambda kv: -kv[1]):
            print(f"  {rule:24} {count}")

    e2e = [s.e2e_ms for s in sessions if s.e2e_ms is not None]
    if e2e:
        st = _stats(e2e)
        verdict = "OK" if st["maks"] < 100 else "OVER MÅL"
        print(f"\nEnde-til-ende latens ({verdict}, mål <100 ms):")
        print(f"  min={st['min']:.1f}  median={st['median']:.1f}  "
              f"p95={st['p95']:.1f}  maks={st['maks']:.1f} ms")

    asr = [s.asr_ms for s in sessions if s.asr_ms is not None]
    if asr:
        st = _stats(asr)
        print(f"\nASR-inferens (mål <20 ms):  min={st['min']:.1f}  median={st['median']:.1f}  "
              f"p95={st['p95']:.1f}  maks={st['maks']:.1f} ms")

    parse = [s.parse_ms for s in sessions if s.parse_ms is not None]
    if parse:
        worst = max(parse)
        print(f"Parsing (mål <2 ms):       maks={worst:.3f} ms  "
              f"({'OK' if worst < 2 else 'OVER MÅL'})")

    good_clips = [(s.clip_ms, s.rms_dbfs, s.speech_ratio, s.loud_ms) for s in matched
                  if s.clip_ms and s.rms_dbfs is not None]
    if good_clips:
        durs = [c[0] for c in good_clips]
        rmss = [c[1] for c in good_clips]
        print(f"\nGodkjente klipp: varighet min={min(durs)} median={statistics.median(durs):.0f} "
              f"maks={max(durs)} ms; RMS lavest={min(rmss):.1f} dBFS")
        hints = [f"rms_threshold_dbfs <= {max(-60.0, min(rmss) - 5):.0f}"]
        louds = [c[3] for c in good_clips if c[3] is not None]
        if louds:
            print(f"  tale-innhold: min={min(louds)} median={statistics.median(louds):.0f} "
                  f"maks={max(louds)} ms")
            hints.append(f"min_loud_ms <= {max(20, round(min(louds) * 0.6))}")
        ratios = [c[2] for c in good_clips if c[2] is not None]
        if ratios:
            hints.append(f"(gammelt format) min_speech_ratio <= {min(ratios) * 0.6:.2f}")
        print(f"Gate-forslag: {'; '.join(hints)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
