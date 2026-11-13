"""WER / CER as corpus-level micro-averages (total edits ÷ total reference length), like the public benchmark.
Empty hypotheses count as full deletions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .normalize import prepare


def edit_distance(ref: Sequence, hyp: Sequence) -> int:
    """Levenshtein distance (substitutions + deletions + insertions), O(len(ref) * len(hyp)) time, O(len(hyp)) memory."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


@dataclass
class Score:
    word_edits: int = 0
    ref_words: int = 0
    char_edits: int = 0
    ref_chars: int = 0

    def add(self, reference: str, hypothesis: str) -> "Score":
        rw, hw = prepare(reference), prepare(hypothesis)
        self.word_edits += edit_distance(rw, hw)
        self.ref_words += len(rw)
        rc, hc = " ".join(rw), " ".join(hw)
        self.char_edits += edit_distance(rc, hc)
        self.ref_chars += len(rc)
        return self

    @property
    def wer(self) -> float:
        return self.word_edits / self.ref_words if self.ref_words else 0.0

    @property
    def cer(self) -> float:
        return self.char_edits / self.ref_chars if self.ref_chars else 0.0


def score(pairs: Sequence[tuple[str, str]]) -> Score:
    s = Score()
    for ref, hyp in pairs:
        s.add(ref, hyp)
    return s
