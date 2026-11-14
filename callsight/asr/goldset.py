"""Pick and cut the gold-set clips: 60 s from the middle of real answered team calls, balanced over agents."""
from __future__ import annotations

import random
import wave
import wave as _wave
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Call, GoldClip

GOLD_DIR = "goldset"


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def cut_wav(src: Path, dest: Path, start_s: float, seconds: float) -> float:
    """Copy `seconds` of audio from `start_s` into a new WAV (same format). Returns the real length written."""
    with wave.open(str(src), "rb") as r:
        rate, total = r.getframerate(), r.getnframes()
        first = max(0, min(int(start_s * rate), total))
        count = max(0, min(int(seconds * rate), total - first))
        r.setpos(first)
        frames = r.readframes(count)
        params = r.getparams()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with wave.open(str(tmp), "wb") as w:
        w.setparams(params)
        w.writeframes(frames)
    tmp.replace(dest)
    return count / rate


def _long_enough(path: Path, seconds: float) -> bool:
    try:
        return wav_seconds(path) >= seconds
    except (OSError, EOFError, _wave.Error):
        return False


def candidates(session: Session, agents: list[str], min_talk: float) -> dict[str, list[Call]]:
    """Answered inbound/outbound team calls with a downloaded recording and enough talk time, per agent."""
    rows = session.scalars(
        select(Call).where(Call.status == "answered", Call.recording_status == "ok",
                           Call.direction.in_(("inbound", "outbound")), Call.agent_ext.in_(agents),
                           Call.talk_seconds >= min_talk)
        .order_by(Call.id)).all()
    out: dict[str, list[Call]] = defaultdict(list)
    for c in rows:
        out[c.agent_ext].append(c)
    return out


def pick(session: Session, audio_dir: Path, agents: list[str], n: int = 30, seconds: float = 60.0,
         seed: int = 1, margin: float = 15.0) -> list[GoldClip]:
    """Choose n calls round-robin over the agents (fixed seed -> same choice), cut the middle `seconds` of each."""
    if session.scalar(select(GoldClip.id).limit(1)) is not None:
        raise ValueError("gold set already exists; it is kept so typed texts are never lost")
    rng = random.Random(seed)
    pools = candidates(session, agents, seconds + margin)
    for pool in pools.values():
        rng.shuffle(pool)
    order = [a for a in agents if pools.get(a)]
    if not order:
        raise ValueError("no suitable calls (answered, recorded, long enough) for these agents")

    chosen: list[Call] = []
    seen_numbers: set[str] = set()
    while len(chosen) < n and any(pools[a] for a in order):
        for a in order:
            while pools[a]:
                c = pools[a].pop()
                if c.customer_number and c.customer_number in seen_numbers:
                    continue                              # one clip per customer
                if not _long_enough(audio_dir / c.recording_path, seconds):
                    continue                              # talk time said long enough, the file is not (or broken)
                chosen.append(c)
                if c.customer_number:
                    seen_numbers.add(c.customer_number)
                break
            if len(chosen) >= n:
                break

    clips = []
    for i, c in enumerate(chosen, 1):
        src = audio_dir / c.recording_path
        dur = wav_seconds(src)
        start = max(0.0, (dur - seconds) / 2)
        rel = Path(GOLD_DIR) / f"clip_{i:02d}_call_{c.id}.wav"
        real = cut_wav(src, audio_dir / rel, start, seconds)
        clip = GoldClip(call_id=c.id, start_seconds=round(start, 2), seconds=round(real, 2), file=rel.as_posix())
        session.add(clip)
        clips.append(clip)
    session.commit()
    return clips
