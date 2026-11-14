"""Silence metrics from the recording (decision 11): fixed dBFS threshold on RMS frames, no diarization.

total silence share, longest silence, number of silences >= min_silence_s and > long_silence_s ("hold-like").
Hold music is sound, so it is not counted as silence. Talk ratio and interruptions need diarization (later).
Pure Python (math.sumprod is C): runs in the normal worker image, no numpy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from ..asr.models import read_wav
from .settings import SilenceRules

FULL_SCALE = 32768.0


@dataclass(frozen=True)
class Metrics:
    audio_seconds: float
    silence_seconds: float
    silence_share: float
    longest_silence_s: float
    silences: int
    long_silences: int


def frame_dbfs(samples, rate: int, frame_ms: int) -> list[float]:
    n = max(1, int(rate * frame_ms / 1000))
    out = []
    for i in range(0, len(samples) - n + 1, n):
        f = samples[i:i + n]
        rms = math.sqrt(math.sumprod(f, f) / n) / FULL_SCALE
        out.append(20 * math.log10(rms) if rms > 0 else -120.0)
    return out


def compute(samples, rate: int, rules: SilenceRules) -> Metrics:
    frame_s = rules.frame_ms / 1000
    runs, run = [], 0
    for db in frame_dbfs(samples, rate, rules.frame_ms):
        if db < rules.threshold_dbfs:
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)
    lengths = [r * frame_s for r in runs if r * frame_s >= rules.min_silence_s]
    total = len(samples) / rate if rate else 0.0
    silent = sum(lengths)
    return Metrics(audio_seconds=round(total, 2), silence_seconds=round(silent, 2),
                   silence_share=round(silent / total, 4) if total else 0.0,
                   longest_silence_s=round(max(lengths, default=0.0), 2), silences=len(lengths),
                   long_silences=sum(1 for x in lengths if x > rules.long_silence_s))


def from_file(path: Path, rules: SilenceRules) -> Metrics:
    samples, rate = read_wav(path)
    return compute(samples, rate, rules)
