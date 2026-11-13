"""Cut long audio into model-sized windows and join the texts back without doubling the overlap."""
from __future__ import annotations


def chunk_bounds(n_samples: int, rate: int, window_s: float = 30.0, overlap_s: float = 1.0) -> list[tuple[int, int]]:
    """[(start, end)] sample ranges of at most `window_s`, consecutive windows overlapping by `overlap_s`."""
    if n_samples <= 0:
        return []
    win, ov = int(window_s * rate), int(overlap_s * rate)
    if ov >= win:
        raise ValueError("overlap must be shorter than the window")
    out, start = [], 0
    while True:
        end = min(start + win, n_samples)
        out.append((start, end))
        if end >= n_samples:
            return out
        start = end - ov


def merge_texts(parts: list[str], max_overlap_words: int = 8) -> str:
    """Join chunk texts; when the end of one chunk repeats at the start of the next (the overlap), keep it once."""
    words: list[str] = []
    for part in parts:
        new = part.split()
        best = 0
        for k in range(min(max_overlap_words, len(words), len(new)), 0, -1):
            if words[-k:] == new[:k]:
                best = k
                break
        words.extend(new[best:])
    return " ".join(words)
