"""Speaker split of one call (spec 2026-11-17-diarization): agent / customer lines and talk metrics.

Pure functions (no torch): the pyannote wrapper `Pyannote` at the end imports its libraries lazily, so tests run on
CPU with fake turns. Decision A (3 judges): Whisper text is unchanged; each word (with its DTW time) is given to the
pyannote speaker whose turn covers it; same-speaker words in a row become one line. Metrics come from the pyannote
turns, never from word times (word times are +-0.3 s).
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass, field

from .normalize import normalize

AGENT, CUSTOMER, UNKNOWN = "agent", "customer", "unknown"
GREETING_S = 10.0                        # the agent greets in the first 10 s (requirement 2)
INTERRUPT_START_S = 0.3                  # a turn starting > 0.3 s into the other's turn ...
INTERRUPT_OVERLAP_S = 1.0                # ... and overlapping it by > 1 s counts as an interruption
# Normalised (same folding as the transcript search): greeting / institute words the agent says first.
GREETINGS = ("السلام عليكم", "معك", "معاك", "المعهد السعودي", "معهد السعودي", "حياك", "حياكم", "اهلا وسهلا",
             "مرحبا", "صباح الخير", "مساء الخير", "كيف اقدر اساعدك", "كيف اخدمك", "تفضل")
INSTITUTE = ("المعهد السعودي", "معهد السعودي")
MIN_TURN_S = 0.3                         # a shorter first turn (click / breath) is not "the first speaker"
RULES_VERSION = "g2"                     # change with the greeting list / naming rules


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float
    seq: int = 0                         # transcript window it came from


@dataclass(frozen=True)
class Turn:
    speaker: str                         # raw pyannote label (SPEAKER_00 / SPEAKER_01)
    start: float
    end: float


@dataclass
class Line:
    speaker: str                         # raw label first, then agent / customer / unknown after naming
    start: float
    end: float
    words: list[str] = field(default_factory=list)
    seq: int = 0                         # window of its first word

    @property
    def text(self) -> str:
        return " ".join(self.words)


@dataclass(frozen=True)
class Naming:
    agent: str | None                    # raw label of the agent, None = unsure
    reason: str                          # first_greets / inbound_first / says_institute / greets / no_greeting / one_speaker

    @property
    def unsure(self) -> bool:
        return self.agent is None


@dataclass(frozen=True)
class Metrics:
    agent_talk_s: float
    customer_talk_s: float
    talk_ratio: float | None             # agent share of speech, 0..1 (None: nobody spoke / unsure naming)
    interruptions_agent: int             # agent started over the customer
    interruptions_customer: int
    longest_wait_s: float                # longest silence after the customer stopped, before anyone spoke again


def rules_hash() -> str:
    return hashlib.sha256(("|".join(GREETINGS) + RULES_VERSION).encode("utf-8")).hexdigest()[:6]


def _speaker_of(w: Word, turns: list[Turn]) -> str | None:
    """The turn overlapping the word most; no overlap: the turn nearest to the word's midpoint."""
    if not turns:
        return None
    best, best_ov = None, 0.0
    for t in turns:
        ov = min(w.end, t.end) - max(w.start, t.start)
        if ov > best_ov:
            best, best_ov = t.speaker, ov
    if best is not None:
        return best
    mid = (w.start + w.end) / 2
    return min(turns, key=lambda t: 0.0 if t.start <= mid <= t.end else min(abs(mid - t.start), abs(mid - t.end))).speaker


def assign(words: list[Word], turns: list[Turn]) -> list[Line]:
    """Words in time order -> lines of one speaker each (raw labels; UNKNOWN when there are no turns)."""
    turns = sorted(turns, key=lambda t: t.start)
    lines: list[Line] = []
    for w in sorted(words, key=lambda x: (x.start, x.seq)):
        sp = _speaker_of(w, turns) or UNKNOWN
        if lines and lines[-1].speaker == sp:
            lines[-1].words.append(w.text)
            lines[-1].end = max(lines[-1].end, w.end)
        else:
            lines.append(Line(sp, w.start, w.end, [w.text], w.seq))
    return lines


def _has(text: str, phrases) -> bool:
    t = f" {normalize(text)} "
    return any(f" {normalize(p)} " in t for p in phrases)


def first_turn(turns: list[Turn]) -> Turn:
    """The first real turn: the earliest one of at least MIN_TURN_S (a 0.1 s click before the "ألو" does not count)."""
    real = [t for t in turns if t.end - t.start >= MIN_TURN_S]
    return min(real or turns, key=lambda t: t.start)


def name_speakers(lines: list[Line], turns: list[Turn], direction: str) -> Naming:
    """Requirement 2: who is the agent (rules g2, from the 30-call first-speaker check of 2026-11-22).
    Inbound: the first speaker (the agent answered first in 20 of 20 checked calls; a greeting is not required).
    Outbound / internal: the first to say the institute's name (the customer also greets: "ألو، السلام عليكم"),
    else the first to greet. Anything else = unsure (the page then shows "متحدث 1 / 2")."""
    speakers = sorted({t.speaker for t in turns})
    if len(speakers) < 2:
        return Naming(None, "one_speaker")
    first_s = first_turn(turns).speaker
    if direction == "inbound":
        early = " ".join(ln.text for ln in lines if ln.start < GREETING_S and ln.speaker == first_s)
        return Naming(first_s, "first_greets" if _has(early, GREETINGS) else "inbound_first")
    ordered = [ln for ln in sorted(lines, key=lambda x: x.start) if ln.speaker in speakers]
    for phrases, why in ((INSTITUTE, "says_institute"), (GREETINGS, "greets")):
        for ln in ordered:
            if _has(ln.text, phrases):
                return Naming(ln.speaker, why)
    return Naming(None, "no_greeting")


def label(lines: list[Line], naming: Naming) -> list[Line]:
    """Raw labels -> agent / customer. Unsure naming: speaker_1 / speaker_2 in order of first appearance (the page
    shows "متحدث 1 / 2")."""
    order = list(dict.fromkeys(ln.speaker for ln in lines if ln.speaker != UNKNOWN))
    out = []
    for ln in lines:
        if ln.speaker == UNKNOWN:
            sp = UNKNOWN
        elif naming.agent is None:
            sp = f"speaker_{order.index(ln.speaker) + 1}" if order.index(ln.speaker) < 2 else UNKNOWN
        else:
            sp = AGENT if ln.speaker == naming.agent else CUSTOMER
        out.append(Line(sp, ln.start, ln.end, list(ln.words), ln.seq))
    return out


def _union_seconds(spans: list[tuple[float, float]]) -> float:
    total, cur_s, cur_e = 0.0, None, None
    for s, e in sorted(spans):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def metrics(turns: list[Turn], naming: Naming) -> Metrics:
    """Talk ratio, interruptions and the longest wait after the customer, from the pyannote turns."""
    if naming.agent is None or not turns:
        talk = _union_seconds([(t.start, t.end) for t in turns])
        return Metrics(round(talk, 2), 0.0, None, 0, 0, 0.0)
    agent = [t for t in turns if t.speaker == naming.agent]
    cust = [t for t in turns if t.speaker != naming.agent]
    a_s, c_s = _union_seconds([(t.start, t.end) for t in agent]), _union_seconds([(t.start, t.end) for t in cust])
    ratio = round(a_s / (a_s + c_s), 3) if a_s + c_s > 0 else None

    def interrupts(by: list[Turn], over: list[Turn]) -> int:
        n = 0
        for b in by:
            for o in over:
                if o.start + INTERRUPT_START_S < b.start < o.end and min(b.end, o.end) - b.start > INTERRUPT_OVERLAP_S:
                    n += 1
                    break
        return n

    wait, ordered = 0.0, sorted(turns, key=lambda t: t.start)
    for t in cust:
        later = [x.start for x in ordered if x.start >= t.end]
        busy = any(x.start < t.end < x.end for x in ordered if x is not t)
        if later and not busy:
            wait = max(wait, min(later) - t.end)
    return Metrics(round(a_s, 2), round(c_s, 2), ratio, interrupts(agent, cust), interrupts(cust, agent),
                   round(wait, 2))


def split_call(words: list[Word], turns: list[Turn], direction: str) -> tuple[list[Line], Naming, Metrics]:
    lines = assign(words, turns)
    naming = name_speakers(lines, turns, direction)
    return label(lines, naming), naming, metrics(turns, naming)


# ---------- pyannote on the GPU (asr image only) ----------

@contextmanager
def full_torch_load(torch):
    """torch >= 2.6 loads with weights_only=True by default, and the pyannote 3.x checkpoints (pickled
    Specifications / TorchVersion objects) then fail with UnpicklingError. Only while our own pinned, local model
    files load (Pyannote.load, under the engine lock) torch.load runs with weights_only=False; restored after."""
    original = torch.load

    def load(*args, **kwargs):
        kwargs["weights_only"] = False
        return original(*args, **kwargs)

    torch.load = load
    try:
        yield
    finally:
        torch.load = original


class Pyannote:
    """pyannote/speaker-diarization-3.1, 2 speakers, from the local HF cache (HF_HUB_OFFLINE=1 in the image)."""
    repo = "pyannote/speaker-diarization-3.1"
    revision = "84fd25912480287da0247647c3d2b4853cb3ee5d"     # pinned 2026-11-19

    # the two models the pipeline config names, pinned too (their "main" ref is never used offline)
    parts = {"segmentation": ("pyannote/segmentation-3.0", "e66f3d3b9eb0873085418a7b813d3b369bf160bb"),
             "embedding": ("pyannote/wespeaker-voxceleb-resnet34-LM", "837717ddb9ff5507820346191109dc79c958d614")}

    def __init__(self) -> None:
        self.pipeline = None

    def load(self) -> None:
        """From the HF cache only (downloaded by gpu-setup build-models): the pipeline config with its two model
        names replaced by the local checkpoint files of the pinned revisions."""
        import tempfile
        from pathlib import Path

        import torch
        import yaml
        from huggingface_hub import snapshot_download
        from pyannote.audio import Pipeline
        local = lambda repo, rev: Path(snapshot_download(repo, revision=rev, local_files_only=True))  # noqa: E731
        cfg = yaml.safe_load((local(self.repo, self.revision) / "config.yaml").read_text(encoding="utf-8"))
        for key, (repo, rev) in self.parts.items():
            cfg["pipeline"]["params"][key] = str(local(repo, rev) / "pytorch_model.bin")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.yaml"
            path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
            with full_torch_load(torch):
                pipeline = Pipeline.from_pretrained(str(path))
        if pipeline is None:
            raise RuntimeError("pyannote pipeline did not load")
        self.pipeline = pipeline.to(torch.device("cuda"))

    def turns(self, audio16k, rate: int = 16000) -> list[Turn]:
        import numpy as np
        import torch
        if self.pipeline is None:
            self.load()
        wav = torch.from_numpy(np.asarray(audio16k, dtype=np.float32)).unsqueeze(0)
        ann = self.pipeline({"waveform": wav, "sample_rate": rate}, num_speakers=2)
        return [Turn(str(sp), round(seg.start, 3), round(seg.end, 3))
                for seg, _, sp in ann.itertracks(yield_label=True)]

    def unload(self) -> None:
        self.pipeline = None

    @property
    def version(self) -> str:
        return f"pa31-{self.revision[:7]}-{rules_hash()}"
