"""Cut long audio into model-sized windows and join the texts back without doubling the overlap."""
from __future__ import annotations

import math

from .normalize import normalize


def chunk_bounds(n_samples: int, rate: int, window_s: float = 30.0, overlap_s: float = 1.0) -> list[tuple[int, int]]:
    """[(start, end)] sample ranges of at most `window_s`, consecutive windows overlapping by `overlap_s`.
    The windows are spread evenly (60 s -> 2 x 30.5 s would exceed 30 s, so 3 x ~20.7 s), never leaving a
    tiny tail window that holds only overlap (models invent text on such scraps)."""
    if n_samples <= 0:
        return []
    win, ov = int(window_s * rate), int(overlap_s * rate)
    if ov >= win:
        raise ValueError("overlap must be shorter than the window")
    if n_samples <= win:
        return [(0, n_samples)]
    n = math.ceil((n_samples - ov) / (win - ov))
    step = (n_samples - ov) / n                      # <= win - ov, so each window <= win
    bounds = [(round(i * step), round(i * step + step + ov)) for i in range(n)]
    bounds[-1] = (bounds[-1][0], n_samples)
    return bounds


def merge_texts(parts: list[str], max_overlap_words: int = 8) -> str:
    """Join chunk texts; when the end of one chunk repeats at the start of the next (the overlap), keep it once."""
    words: list[str] = []
    for part in parts:
        new = part.split()
        best = 0
        for k in range(min(max_overlap_words, len(words), len(new)), 0, -1):
            if [normalize(w) for w in words[-k:]] == [normalize(w) for w in new[:k]]:
                best = k
                break
        words.extend(new[best:])
    return " ".join(words)
