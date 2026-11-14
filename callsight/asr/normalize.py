"""Arabic text normalisation before scoring, following the `echonova-v1` rules of
echonova-ai-solutions/arabic-asr-benchmark (Apache-2.0), so our WER compares with their public table:
remove punctuation and harakat, fold alif/hamza forms, ة→ه, پ→ب, ڤ→ف, Eastern→Western digits, drop standalone hamza.
ى is NOT folded; numbers are not converted between digits and words.
"""
from __future__ import annotations

import re
import unicodedata

_HARAKAT = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭـ]")   # tashkeel, Quranic marks, tatweel
_FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ؤ": "و", "ئ": "ي",
    "ة": "ه",
    "پ": "ب", "ڤ": "ف",
    **{chr(0x0660 + i): str(i) for i in range(10)},   # Arabic-Indic digits
    **{chr(0x06F0 + i): str(i) for i in range(10)},   # Persian digits
})
_HAMZA = re.compile("ء")
_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)       # anything that is not a letter/digit/space
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "")
    t = _HARAKAT.sub("", t)
    t = t.translate(_FOLD)
    t = _HAMZA.sub("", t)
    t = _PUNCT.sub(" ", t)
    t = t.casefold()                                # code-switched English words
    return _SPACES.sub(" ", t).strip()


def cap_repeats(words: list[str], max_repeats: int = 10, max_unit: int = 3) -> list[str]:
    """`sada-paper` protocol: a 1-3 word unit repeated more than 10 times in a row is cut to 10 copies
    (stops one hallucination loop from dominating the error count)."""
    out = list(words)
    for unit in range(1, max_unit + 1):
        i, res = 0, []
        while i < len(out):
            chunk = out[i:i + unit]
            n = 1
            while out[i + n * unit:i + (n + 1) * unit] == chunk and len(chunk) == unit:
                n += 1
            if n > max_repeats:
                res.extend(chunk * max_repeats)
                i += n * unit
            else:
                res.append(out[i])
                i += 1
        out = res
    return out


def prepare(text: str, cap: bool = False) -> list[str]:
    """Normalised word list used for scoring. Strict echonova-v1 by default (no repeat cap, so hallucination
    loops count fully); `cap=True` gives the sada-paper variant shown as an extra column."""
    words = normalize(text).split()
    return cap_repeats(words) if cap else words
