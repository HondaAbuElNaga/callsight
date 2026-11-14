"""Celery task of the `nlp` worker: analyse one call (spec 2026-11-14-call-analysis).

Run in the normal app image (HTTP to Ollama only, no torch):
    celery -A callsight.analysis.tasks worker -Q nlp --concurrency=1
Order inside one task: flags (rules.yaml) + silence metrics (audio) -> skip check -> GPU lock -> Ollama -> save.
Privacy (requirement 3): results and logs hold ids, counts and reasons only - never transcript text, summary or
reason, also not on exceptions (their messages are never logged; only the exception type).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .. import app as wiring
from ..db.models import Call, CallAnalysis, CallAudioMetrics, TranscriptSegment
from ..jobs import celery, send_analyze
from . import flags, llm, queueing, silence
from .settings import LlmConfig, Rules, load_rules, load_topics

log = logging.getLogger(__name__)
GPU_WAIT_S = 120                 # GPU held by the asr worker: try again in 2 min (no try counted)
NOT_READY_S = 300                # Ollama not reachable: try again in 5 min (no try counted)
MAINT_WAIT_S = 300
UNLOAD_WAIT_S = 15               # after the answer: wait until Ollama has left the GPU before releasing the lock


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Deps:
    """Everything `process` needs from outside, injectable in tests (fake Ollama, fake Redis)."""
    cfg: LlmConfig
    topics: dict[str, str]
    rules: Rules
    client: llm.Ollama
    gpu: Callable                  # () -> context manager yielding True when the GPU lock is ours
    audio_dir: Path
    asr_model: str

    @property
    def prompt_version(self) -> str:
        return llm.prompt_version(self.topics)


def dropped_share(segs, audio_seconds: float | None) -> float:
    total = audio_seconds or (max((s.end_seconds for s in segs), default=0.0))
    if not total:
        return 1.0
    dropped = sum(max(0.0, s.end_seconds - s.start_seconds) for s in segs if s.flag)
    return dropped / total


def _requeue(session: Session, row: CallAnalysis, now: datetime, due_in_s: int) -> None:
    """Back to `queued` without using up a try (the call itself was never tried)."""
    row.status, row.queued_at, row.started_at = "queued", now + timedelta(seconds=due_in_s), None
    row.tries = max(0, row.tries - 1)
    session.commit()


def _finish(session: Session, row: CallAnalysis, status: str, message: str, now: datetime) -> dict:
    row.status, row.error, row.finished_at = status, message[:300], now
    session.commit()
    if status == "error":
        log.warning("analysis of call %s failed: %s", row.call_id, message)
    return {"call_id": row.call_id, "status": status}


def _metrics(session: Session, call: Call, deps: Deps) -> None:
    """Silence metrics; never fails the analysis (a missing/odd file only skips them)."""
    if call.recording_status != "ok" or not call.recording_path:
        return
    base = deps.audio_dir.resolve()
    path = (base / call.recording_path).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        return
    try:
        m = silence.from_file(path, deps.rules.silence)
    except Exception as exc:                     # noqa: BLE001
        log.warning("silence metrics for call %s skipped: %s", call.id, type(exc).__name__)
        return
    values = dict(metrics_version=deps.rules.silence.version, audio_seconds=m.audio_seconds,
                  silence_seconds=m.silence_seconds, silence_share=m.silence_share,
                  longest_silence_s=m.longest_silence_s, silences=m.silences, long_silences=m.long_silences)
    session.execute(insert(CallAudioMetrics).values(call_id=call.id, **values)
                    .on_conflict_do_update(index_elements=["call_id"], set_=values))


def process(session: Session, call_id: int, model: str, pv: str, deps: Deps,
            now: Callable[[], datetime] = _utcnow) -> dict:
    from ..transcripts import displayed
    row = session.scalars(select(CallAnalysis).where(CallAnalysis.call_id == call_id, CallAnalysis.model == model,
                                                     CallAnalysis.prompt_version == pv)).first()
    if row is None or row.status != "queued":
        return {"call_id": call_id, "status": row.status if row else None, "skipped": True}
    if (model, pv) != (deps.cfg.model, deps.prompt_version):   # model/prompt/topics changed after queueing
        _finish(session, row, "skipped", "outdated: model or prompt changed before analysis", now())
        return {"call_id": call_id, "status": "skipped", "outdated": True}
    if not queueing.claim(session, row.id, now()):
        return {"call_id": call_id, "status": "taken", "skipped": True}
    session.refresh(row)

    t = displayed(session, call_id, deps.asr_model)
    if t is None or t.status != "done":
        return _finish(session, row, "error", "no done transcript", now())
    row.transcript_id = t.id
    segs = session.scalars(select(TranscriptSegment).where(TranscriptSegment.transcript_id == t.id)
                           .order_by(TranscriptSegment.seq)).all()
    try:
        n_flags = flags.store(session, call_id, t, deps.rules)
        _metrics(session, session.get(Call, call_id), deps)
        session.commit()
    except Exception as exc:                     # noqa: BLE001  DB errors may quote values: type only
        session.rollback()
        return _finish(session, row, "error", f"flags/metrics failed ({type(exc).__name__})", now())

    share = dropped_share(segs, t.audio_seconds)
    if share > deps.rules.max_dropped_share:
        return _finish(session, row, "skipped",
                       f"{share:.0%} of the call in dropped windows (max {deps.rules.max_dropped_share:.0%})", now())
    if not llm.transcript_lines(segs):
        return _finish(session, row, "skipped", "no kept transcript text", now())

    with deps.gpu() as got:
        if not got:
            _requeue(session, row, now(), GPU_WAIT_S)
            return {"call_id": call_id, "status": "queued", "gpu_busy": True, "flags": n_flags}
        try:
            res = llm.analyze(segs, deps.topics, deps.client, deps.cfg)
        except llm.LlmUnavailable as exc:
            _requeue(session, row, now(), NOT_READY_S)
            log.warning("analysis of call %s left queued: %s", call_id, exc)
            return {"call_id": call_id, "status": "queued", "not_ready": True}
        except llm.LlmError as exc:
            return _finish(session, row, "error", str(exc), now())
        except Exception as exc:                 # noqa: BLE001  message may quote model output
            return _finish(session, row, "error", f"analysis failed ({type(exc).__name__})", now())
        finally:
            _wait_unloaded(deps.client)

    o = res.output
    try:
        row.summary, row.topic, row.action_items = o.summary, o.topic, list(o.action_items)
        row.llm_score, row.reason, row.escalation = o.satisfaction, o.reason, o.escalation
        row.trimmed, row.prompt_tokens, row.compute_seconds = res.trimmed, res.prompt_tokens, res.seconds
        row.status, row.error, row.finished_at = "done", None, now()
        session.commit()
    except Exception as exc:                     # noqa: BLE001
        session.rollback()
        return _finish(session, row, "error", f"saving analysis failed ({type(exc).__name__})", now())
    log.info("analysis of call %s done: %s tokens, %.1f s, %s tries, trimmed=%s, %s flags",
             call_id, res.prompt_tokens, res.seconds, res.tries, res.trimmed, n_flags)
    return {"call_id": call_id, "status": "done", "tries": res.tries, "seconds": res.seconds,
            "trimmed": res.trimmed, "flags": n_flags}


def _wait_unloaded(client: llm.Ollama, wait_s: float = UNLOAD_WAIT_S) -> None:
    """keep_alive=0 unloads right after the answer, but asynchronously: keep the GPU lock until it is gone."""
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        try:
            if not client.loaded_models():
                return
        except Exception:                        # noqa: BLE001
            return
        time.sleep(0.5)


# ---------- wiring (worker only) ----------

def _redis():
    from ..jobs import redis_client
    return redis_client()


def _gpu_lock(ttl: int):
    """GPU lock for one Ollama answer. Redis trouble = no lock = wait (never risk two models on 8 GB).
    Transcription goes first: while an asr task is waiting for the GPU (`gpu_wanted`), the lock is not taken."""
    from contextlib import contextmanager

    from ..locks import gpu_acquire, gpu_release, gpu_wanted

    @contextmanager
    def cm():
        try:
            r = _redis()
            got = False if gpu_wanted(r) else gpu_acquire(r, "nlp", ttl)
        except Exception as exc:                 # noqa: BLE001
            log.warning("GPU lock not available: %s", type(exc).__name__)
            yield False
            return
        try:
            yield got
        finally:
            if got:
                try:
                    gpu_release(r, "nlp")
                except Exception:                # noqa: BLE001  expires on its own
                    log.warning("GPU lock release failed; it expires on its own")
    return cm


def deps_from_env() -> Deps:
    import os
    cfg = LlmConfig.from_env()
    return Deps(cfg=cfg, topics=load_topics(cfg.topics_file), rules=load_rules(cfg.rules_file),
                client=llm.Ollama(cfg), gpu=_gpu_lock(int(cfg.timeout_s * llm.MAX_TRIES) + 120),
                audio_dir=wiring.settings().audio_dir,
                asr_model=os.getenv("ASR_MODEL", "whisper-large-v3").strip())


def _maint_busy() -> bool:
    from ..locks import maint_busy
    try:
        return maint_busy(_redis())
    except Exception:                            # noqa: BLE001
        return False


def _park(call_id: int, model: str, pv: str, due_in_s: int) -> None:
    """A parked (countdown) message is outside the Redis list: move `queued_at` to when it is due, so the reaper
    doesn't count the row as lost while backup/retention runs (same as the asr task). Never fails the task."""
    try:
        with wiring.session_factory()() as s:
            s.execute(update(CallAnalysis).where(CallAnalysis.call_id == call_id, CallAnalysis.model == model,
                                                 CallAnalysis.prompt_version == pv, CallAnalysis.status == "queued")
                      .values(queued_at=_utcnow() + timedelta(seconds=due_in_s)))
            s.commit()
    except Exception:                            # noqa: BLE001
        log.warning("call %s: queued_at not moved while waiting for maintenance", call_id)


@celery.task(name=queueing.TASK_NAME, soft_time_limit=15 * 60, time_limit=16 * 60, ignore_result=False)
def analyze_call(call_id: int, model: str | None = None, prompt_version: str | None = None) -> dict:
    deps = deps_from_env()
    model, pv = model or deps.cfg.model, prompt_version or deps.prompt_version
    if _maint_busy():
        _park(call_id, model, pv, MAINT_WAIT_S)
        send_analyze(call_id, model, pv, countdown=MAINT_WAIT_S)
        return {"call_id": call_id, "status": "queued", "maint_wait": True}
    with wiring.session_factory()() as s:
        r = process(s, call_id, model, pv, deps)
        if r.get("outdated"):                    # queue the current model/prompt instead
            if queueing.add_row(s, call_id, deps.cfg.model, deps.prompt_version, _utcnow()) == "queued":
                s.commit()
                send_analyze(call_id, deps.cfg.model, deps.prompt_version)
            else:
                s.commit()
    if r.get("gpu_busy"):
        send_analyze(call_id, model, pv, countdown=GPU_WAIT_S)
    elif r.get("not_ready"):
        send_analyze(call_id, model, pv, countdown=NOT_READY_S)
    return r
