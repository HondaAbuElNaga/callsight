"""Transcribe one whole call with the model named by ASR_MODEL (spec 2026-11-14-transcription, decision 1).

WAV -> 16 kHz -> evenly spread windows of <= 30 s with 1 s overlap (`chunk_bounds`) -> one segment per window.
Windows that are near-silent are not sent to the model; texts that look like known Whisper hallucinations are
dropped (the segment keeps a `flag` and empty text). Torch/numpy are only imported by the adapters/resampler,
so this module itself imports anywhere.
"""
from __future__ import annotations

import gc
import logging
import math
import re
import sys
import threading
import time
import wave
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .chunking import chunk_bounds, merge_texts
from .models import MODELS, TARGET_RATE, read_wav, to_16k
from .normalize import normalize

log = logging.getLogger(__name__)

SEGMENTER_VERSION = "w30o1-rms-hf1"      # change when windows/filters change, so old rows can be told apart
WINDOW_S, OVERLAP_S = 30.0, 1.0
SILENCE_RMS = 0.003                      # ~ -50 dBFS: phone line noise; 2 s of speech in a 30 s window is ~0.02
MAX_CHARS_PER_S = 25.0                   # fast Saudi speech is ~15 letters/s; more = model loop/invention
MAX_REPEATS = 4                          # a 1-4 word unit repeated more than 4x in a row = hallucination loop
# Known Whisper hallucinations on silence/noise (learned from YouTube subtitles). Compared after normalize().
BLOCKLIST = tuple(normalize(p) for p in (
    "اشتركوا في القناة", "اشترك في القناة", "لا تنسوا الاشتراك", "لا تنسى الاشتراك",
    "ترجمة نانسي قنقر", "نانسي قنقر", "شكرا للمشاهدة", "شكرا على المشاهدة", "شكرا لكم على المشاهدة",
    "شكرا لحسن الاستماع", "المترجم للقناة", "ترجمة وتدقيق", "سبتايتل",
))


class AudioError(Exception):
    """The recording file can't be used (missing, not a WAV, empty). Terminal: no retry."""


class ModelNotReady(Exception):
    """The model could not be loaded (no cache offline, no CUDA, GPU busy). The worker's fault, not the call's:
    the row goes back to `queued` without counting a try."""


def check_model_name(name: str) -> None:
    if name not in MODELS:
        raise ValueError(f"unknown ASR_MODEL {name!r}; choose one of {', '.join(sorted(MODELS))}")


@dataclass
class Segment:
    seq: int
    start: float
    end: float
    text: str
    flag: str | None = None              # silence / blocklist / repeat / too_fast


@dataclass
class Result:
    segments: list[Segment] = field(default_factory=list)
    text: str = ""
    audio_seconds: float = 0.0
    speech_seconds: float = 0.0          # display time of windows that kept text
    dropped_windows: int = 0


def display_times(bounds: list[tuple[int, int]], rate: int) -> list[tuple[float, float]]:
    """Window sample ranges -> non-overlapping display times: each boundary sits in the middle of the overlap."""
    times = []
    for i, (s, e) in enumerate(bounds):
        start = 0.0 if i == 0 else (s + bounds[i - 1][1]) / 2 / rate
        end = e / rate if i == len(bounds) - 1 else (bounds[i + 1][0] + e) / 2 / rate
        times.append((round(start, 3), round(end, 3)))
    return times


def rms(x) -> float:
    if len(x) == 0:
        return 0.0
    if hasattr(x, "dtype"):              # numpy array in the worker: vectorised
        import numpy as np
        return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return math.sqrt(sum(v * v for v in x) / len(x))


def _max_run(words: list[str], unit: int) -> int:
    """Longest run of one `unit`-word chunk repeated back to back."""
    best = 1
    for i in range(len(words) - unit + 1):
        chunk, n = words[i:i + unit], 1
        while words[i + n * unit:i + (n + 1) * unit] == chunk:
            n += 1
        best = max(best, n)
    return best


def hallucination_flag(text: str, seconds: float) -> str | None:
    """Why this window's text should be dropped, or None to keep it."""
    norm = normalize(text)
    if not norm:
        return None
    if any(p in norm for p in BLOCKLIST):
        return "blocklist"
    words = norm.split()
    if any(_max_run(words, u) > MAX_REPEATS for u in range(1, 5)):
        return "repeat"
    if seconds > 0 and len(norm.replace(" ", "")) / seconds > MAX_CHARS_PER_S:
        return "too_fast"
    return None


def transcribe_audio(adapter, audio, rate: int = TARGET_RATE, silence_rms: float = SILENCE_RMS) -> Result:
    """Run the adapter over every window of one call. Model errors propagate (the task decides retry/error)."""
    bounds = chunk_bounds(len(audio), rate, min(WINDOW_S, adapter.window_s), OVERLAP_S)
    res = Result(audio_seconds=round(len(audio) / rate, 3))
    runs: list[list[str]] = [[]]          # texts of back-to-back kept windows; a dropped window starts a new run
    for i, ((s, e), (start, end)) in enumerate(zip(bounds, display_times(bounds, rate))):
        piece = audio[s:e]
        if rms(piece) < silence_rms:
            seg = Segment(i, start, end, "", "silence")
        else:
            text = (adapter.transcribe(piece) or "").replace("\x00", "").strip()   # Postgres refuses NUL
            flag = hallucination_flag(text, (e - s) / rate)
            seg = Segment(i, start, end, "" if flag else text, flag)
        if seg.flag:
            res.dropped_windows += 1
            runs.append([])
        elif seg.text:
            runs[-1].append(seg.text)
            res.speech_seconds += end - start
        else:
            runs.append([])                # empty window = gap too: no overlap to remove across it
        res.segments.append(seg)
    # Overlap words are only removed between neighbouring windows: across a gap the "repeat" is real speech.
    res.text = " ".join(m for m in (merge_texts(r) for r in runs) if m)
    res.speech_seconds = round(res.speech_seconds, 3)
    return res


def load_audio(path: Path, resample: Callable = to_16k):
    """Recording -> 16 kHz float samples. Bad files raise AudioError with a message that names no content."""
    if not path.is_file():
        raise AudioError("recording file not found")
    try:
        samples, rate = read_wav(path)
    except (wave.Error, EOFError, ValueError) as exc:
        raise AudioError(f"recording is not a readable 16-bit mono WAV ({type(exc).__name__})") from exc
    if len(samples) == 0:
        raise AudioError("recording is empty")
    return resample(samples, rate)


class ModelHolder:
    """One model per worker process: loaded on first use, freed after `idle_unload_s` without work
    (so a later Ollama job can use the 8 GB GPU; decision 8)."""

    def __init__(self, name: str, idle_unload_s: float = 300, factory: Callable | None = None,
                 clock: Callable[[], float] = time.monotonic, start_timer: bool = True, load_retry_s: float = 120,
                 on_unload: Callable[[], None] | None = None,
                 before_use: Callable[[], bool] | None = None):
        self.name, self.idle_unload_s, self.clock, self.load_retry_s = name, idle_unload_s, clock, load_retry_s
        self.on_unload = on_unload               # e.g. release the GPU lock (call-analysis spec, decision 6)
        # e.g. take/refresh the GPU lock. Called INSIDE self.lock right before the model is loaded or used, so the
        # idle-unload thread (which releases that lock, also inside self.lock) can't slip in between.
        self.before_use = before_use
        self.failed_at: float | None = None
        self.factory = factory or self._from_registry
        self.adapter = None
        self.busy = False
        self.last_used = clock()
        self.lock = threading.Lock()
        if start_timer and idle_unload_s > 0:
            threading.Thread(target=self._idle_loop, daemon=True, name="asr-idle-unload").start()

    @staticmethod
    def _from_registry(name: str):
        check_model_name(name)
        return MODELS[name]()

    @property
    def loaded(self) -> bool:
        return self.adapter is not None

    @property
    def revision(self) -> str | None:
        cls = MODELS.get(self.name)
        return getattr(self.adapter, "revision", None) or (cls.revision if cls else None)

    def get(self):
        """The loaded adapter. Any load failure -> ModelNotReady; after one, no new try for `load_retry_s`
        (a queue of 50 calls must not trigger 50 slow failing loads)."""
        if self.adapter is None:
            if self.failed_at is not None and self.clock() - self.failed_at < self.load_retry_s:
                raise ModelNotReady(f"model {self.name} failed to load recently; next try later")
            t = time.perf_counter()
            try:
                adapter = self.factory(self.name)
                adapter.load()
            except Exception as exc:                       # noqa: BLE001
                self.failed_at = self.clock()
                free_gpu()
                raise ModelNotReady(f"model {self.name} could not be loaded ({type(exc).__name__})") from exc
            self.failed_at = None
            log.info("asr model %s loaded in %.0f s", self.name, time.perf_counter() - t)
            self.adapter = adapter
        return self.adapter

    def _claim(self) -> None:
        if self.before_use is not None and not self.before_use():
            raise ModelNotReady("the GPU is in use by another job")     # not a load failure: no retry pause

    def ensure_loaded(self) -> None:
        with self.lock:
            self._claim()
            self.get()
            self.last_used = self.clock()

    @contextmanager
    def use(self):
        with self.lock:
            self._claim()
            self.busy = True
            try:
                yield self.get()
            finally:
                self.busy = False
                self.last_used = self.clock()

    def unload_if_idle(self) -> bool:
        if not self.lock.acquire(blocking=False):
            return False                                  # a call is being transcribed right now
        try:
            if self.adapter is None or self.busy or self.clock() - self.last_used < self.idle_unload_s:
                return False
            self.adapter = None
            free_gpu()
            log.info("asr model %s unloaded after %.0f s idle", self.name, self.idle_unload_s)
            if self.on_unload is not None:
                try:
                    self.on_unload()
                except Exception:                      # noqa: BLE001  the lock expires on its own
                    log.warning("on_unload hook failed")
            return True
        finally:
            self.lock.release()

    def _idle_loop(self) -> None:
        while True:
            time.sleep(max(5.0, min(60.0, self.idle_unload_s / 5)))
            try:
                self.unload_if_idle()
            except Exception:                              # noqa: BLE001  the timer must never die
                log.exception("idle unload failed")


def free_gpu() -> None:
    gc.collect()
    torch = sys.modules.get("torch")                       # only if a model ever imported it
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()
