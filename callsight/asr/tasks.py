"""Celery task of the GPU `asr` worker: transcribe one call (spec 2026-11-14-transcription).

Run only in the asr image:  celery -A callsight.asr.tasks worker -Q asr --concurrency=1
The ingest worker and the API never import this module: they enqueue by name with
`send_task(TASK_NAME)`, so they never need torch. The engine/model are created lazily inside the task.
Privacy (decision 9): results and logs hold ids and counts only, never transcript text.
Only errors of the call itself (bad audio, the model failing on this audio) are terminal; a worker that can't
load its model or runs another model leaves the row `queued` without counting a try.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from celery.signals import worker_init
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .. import app as wiring
from ..db.models import Call, Transcript, TranscriptSegment
from ..jobs import celery, send_transcribe
from . import queueing

log = logging.getLogger(__name__)
_holder = None                                   # one model per worker process
NOT_READY_RETRY_S = 300                          # worker can't load its model: try the call again in 5 min
MAINT_WAIT_S = 300                               # backup/retention running: wait, then try again
OOM_RETRY_S = 60                                # CUDA out of memory: one more try after the cache was freed
GPU_WAIT_S = 120                                 # the nlp task (Ollama) holds the GPU: try again in 2 min
TASK_LIMIT_S = 16 * 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_oom(exc: BaseException) -> bool:
    return type(exc).__name__ == "OutOfMemoryError" or "out of memory" in str(exc).lower()


def _fail(session: Session, row: Transcript, message: str, now: datetime) -> dict:
    row.status, row.error, row.finished_at = "error", message[:300], now
    session.commit()
    log.warning("transcript for call %s failed: %s", row.call_id, message)
    return {"call_id": row.call_id, "status": "error"}


def _requeue(session: Session, row: Transcript, now: datetime, count_try: bool, due_in_s: int = 0) -> None:
    """Back to `queued`. `queued_at` = when the (delayed) re-send becomes due: a countdown message sits unacked
    outside the Redis list (LLEN 0), so the reaper must not mistake the row for a lost message meanwhile."""
    row.status, row.queued_at, row.started_at = "queued", now + timedelta(seconds=due_in_s), None
    if not count_try:
        row.tries = max(0, row.tries - 1)        # claim() counted it, but the call itself was never tried
    session.commit()


def process(session: Session, call_id: int, model: str, holder, audio_dir: Path,
            load: Callable | None = None, now: Callable[[], datetime] = _utcnow) -> dict:
    """Transcribe one call into its `queued` transcript row. Idempotent: anything but `queued` is left alone."""
    from . import engine                         # lazy: numpy/torch only in the asr image
    load = load or engine.load_audio
    row = session.scalars(select(Transcript).where(Transcript.call_id == call_id, Transcript.model == model)).first()
    if row is None or row.status != "queued":
        return {"call_id": call_id, "status": row.status if row else None, "skipped": True}
    if model != holder.name:                     # not this worker's job: leave it for the right worker
        log.warning("call %s is queued for %s but this worker runs ASR_MODEL=%s: left queued",
                    call_id, model, holder.name)
        _requeue(session, row, now(), count_try=True)   # not claimed: tries unchanged; reaper re-sends in 30 min
        return {"call_id": call_id, "status": "queued", "skipped": True, "wrong_model": True}
    try:
        holder.ensure_loaded()                   # before the claim: a broken worker must not use up tries
    except engine.ModelNotReady as exc:
        _requeue(session, row, now(), count_try=True, due_in_s=NOT_READY_RETRY_S)   # not claimed: no try used
        log.warning("call %s left queued: %s", call_id, exc)
        return {"call_id": call_id, "status": "queued", "not_ready": True}
    if not queueing.claim(session, row.id, now()):
        return {"call_id": call_id, "status": "taken", "skipped": True}
    session.refresh(row)

    call = session.get(Call, call_id)
    base = audio_dir.resolve()
    path = (base / (call.recording_path or "")).resolve()
    t0 = time.perf_counter()
    try:
        if not call.recording_path or not path.is_relative_to(base):
            raise engine.AudioError("recording path outside AUDIO_DIR")
        audio = load(path)
        with holder.use() as adapter:
            result = engine.transcribe_audio(adapter, audio)
            split = getattr(holder, "speaker_turns", None)     # diarization spec: None = no speaker split
            turns = split(audio) if split is not None else None
    except engine.AudioError as exc:
        return _fail(session, row, str(exc), now())
    except engine.ModelNotReady as exc:          # model was unloaded and could not be re-loaded
        _requeue(session, row, now(), count_try=False, due_in_s=NOT_READY_RETRY_S)
        log.warning("call %s left queued: %s", call_id, exc)
        return {"call_id": call_id, "status": "queued", "not_ready": True}
    except Exception as exc:                     # noqa: BLE001  model/GPU errors: one retry for OOM only
        engine.free_gpu()
        if _is_oom(exc) and row.tries < queueing.MAX_TRIES:
            _requeue(session, row, now(), count_try=True, due_in_s=OOM_RETRY_S)
            log.warning("transcript for call %s: CUDA out of memory, retrying once", call_id)
            return {"call_id": call_id, "status": "queued", "retry": True}
        if _is_oom(exc):
            return _fail(session, row, "CUDA out of memory", now())
        if type(exc).__name__ == "SoftTimeLimitExceeded":
            return _fail(session, row, "time limit (15 min) exceeded", now())
        return _fail(session, row, f"model failed ({type(exc).__name__})", now())   # message may quote text

    try:
        _save(session, row, result, holder.revision, round(time.perf_counter() - t0, 2), now())
        if turns is not None:
            save_speakers(session, row, result, turns, call.direction, getattr(holder, "diarizer_version", None))
    except Exception as exc:                     # noqa: BLE001  DB errors may quote the text: never re-raise them
        session.rollback()
        return _fail(session, row, f"saving transcript failed ({type(exc).__name__})", now())
    latency = queueing.latency_seconds(row.finished_at, call.end_time)
    log.info("transcript for call %s: %s segments (%s dropped), %.0f s audio in %.1f s, latency %.0f s",
             call_id, len(result.segments), result.dropped_windows, result.audio_seconds, row.compute_seconds, latency)
    return {"call_id": call_id, "status": "done", "segments": len(result.segments),
            "dropped": result.dropped_windows, "audio_s": result.audio_seconds, "latency_s": round(latency)}


def _save(session: Session, row: Transcript, result, revision: str | None, compute_s: float, now: datetime) -> None:
    from .engine import SEGMENTER_VERSION
    session.execute(delete(TranscriptSegment).where(TranscriptSegment.transcript_id == row.id))   # --force redo
    session.add_all(TranscriptSegment(transcript_id=row.id, seq=s.seq, start_seconds=s.start, end_seconds=s.end,
                                      text=s.text, flag=s.flag,
                                      words=[list(w) for w in s.words] if getattr(s, "words", None) else None)
                    for s in result.segments)
    from ..transcripts import search_text
    row.status, row.text, row.error = "done", result.text, None
    row.search_text = search_text(result.segments)        # dropped windows are not searchable
    row.model_revision = revision
    row.audio_seconds, row.speech_seconds = result.audio_seconds, result.speech_seconds
    row.dropped_windows, row.segmenter_version = result.dropped_windows, SEGMENTER_VERSION
    row.compute_seconds, row.finished_at = compute_s, now
    session.commit()


SPEAKER_FIELDS = ("speaker_unsure", "speaker_reason", "agent_talk_s", "customer_talk_s", "talk_ratio",
                  "interruptions_agent", "interruptions_customer", "longest_wait_s", "diarization_version")


def save_speakers(session: Session, row: Transcript, result, turns, direction: str, version: str | None) -> None:
    """pyannote turns + the window words -> agent / customer lines and talk metrics (diarization spec).
    Replaces an earlier split of this transcript. A failure here leaves the transcript itself untouched."""
    from ..db.models import SpeakerTurn, TranscriptTurn
    from . import diarize as dz
    try:
        words = [dz.Word(w, s, e, seg.seq) for seg in result.segments for (w, s, e) in (getattr(seg, "words", None) or [])]
        lines, naming, m = dz.split_call(words, list(turns), direction)
        session.execute(delete(SpeakerTurn).where(SpeakerTurn.transcript_id == row.id))
        session.execute(delete(TranscriptTurn).where(TranscriptTurn.transcript_id == row.id))
        session.add_all(SpeakerTurn(transcript_id=row.id, seq=i, speaker_raw=t.speaker[:20], start_seconds=t.start,
                                    end_seconds=t.end) for i, t in enumerate(sorted(turns, key=lambda t: t.start)))
        session.add_all(TranscriptTurn(transcript_id=row.id, seq=i, speaker=ln.speaker, start_seconds=ln.start,
                                       end_seconds=ln.end, text=ln.text, segment_seq=ln.seq)
                        for i, ln in enumerate(lines))
        row.speaker_unsure, row.speaker_reason = naming.unsure, naming.reason
        row.agent_talk_s, row.customer_talk_s, row.talk_ratio = m.agent_talk_s, m.customer_talk_s, m.talk_ratio
        row.interruptions_agent, row.interruptions_customer = m.interruptions_agent, m.interruptions_customer
        row.longest_wait_s, row.diarization_version = m.longest_wait_s, (version or "unknown")[:30]
        session.commit()
    except Exception as exc:                     # noqa: BLE001  DB errors may quote text: never re-raise them
        session.rollback()
        log.warning("speaker split for call %s not saved (%s)", row.call_id, type(exc).__name__)


def _park(call_id: int, model: str, due_in_s: int) -> None:
    """A parked (countdown) message is outside the Redis list: move `queued_at` to when it is due, so the reaper
    doesn't count it as lost right after maintenance ends. Never fails the task."""
    try:
        with wiring.session_factory()() as s:
            s.execute(update(Transcript).where(Transcript.call_id == call_id, Transcript.model == model,
                                               Transcript.status == "queued")
                      .values(queued_at=_utcnow() + timedelta(seconds=due_in_s)))
            s.commit()
    except Exception:                            # noqa: BLE001
        log.warning("call %s: queued_at not moved while waiting for maintenance", call_id)


def _maint_busy() -> bool:
    """True while backup/retention holds the maintenance lock. Redis trouble never blocks transcription."""
    from ..jobs import redis_client
    from ..locks import maint_busy
    try:
        return maint_busy(redis_client())
    except Exception:                            # noqa: BLE001
        return False


def _gpu_take(idle_unload_s: float) -> bool:
    """GPU lock for the asr worker (call-analysis spec, decision 6): held while the model is loaded, refreshed by
    every task and again right before the model is loaded/used (ModelHolder.before_use), released when the model is
    unloaded (idle), expires on its own if the worker dies.
    DELIBERATE EXCEPTION to "never two models on the GPU": when Redis itself fails, transcription goes on without
    the lock (same as the maintenance lock) - transcripts are the priority, and the nlp side fails closed (no
    Redis = no Ollama answer), so both can only overlap if Redis fails in the middle of an Ollama answer."""
    from ..jobs import redis_client
    from ..locks import gpu_acquire, gpu_unwant
    try:
        r = redis_client()
        got = gpu_acquire(r, "asr", int(idle_unload_s + TASK_LIMIT_S + 60))
    except Exception:                            # noqa: BLE001
        return True
    if got:
        try:
            gpu_unwant(r)                        # we have it: the nlp task may queue up behind us again
        except Exception:                        # noqa: BLE001  the flag expires on its own
            pass
    return got


def _gpu_want() -> None:
    """Tell the nlp task that transcription is waiting for the GPU (it then stops taking the lock)."""
    from ..jobs import redis_client
    from ..locks import gpu_want
    try:
        gpu_want(redis_client(), GPU_WAIT_S * 2 + 60)
    except Exception:                            # noqa: BLE001
        pass


def _gpu_free() -> None:
    from ..jobs import redis_client
    from ..locks import gpu_release
    try:
        gpu_release(redis_client(), "asr")
    except Exception:                            # noqa: BLE001  expires on its own
        log.warning("GPU lock release failed; it expires on its own")


def _queue_analysis(call_id: int) -> None:
    """Transcript done -> analysis queued at once (only when NLP_ENABLED=true). Never fails the transcript."""
    try:
        from ..analysis import llm
        from ..analysis import queueing as nlpq
        from ..analysis.rulestore import current_topics
        from ..analysis.settings import LlmConfig
        from ..jobs import send_analyze
        cfg = LlmConfig.from_env()
        if not cfg.enabled:
            return
        with wiring.session_factory()() as s:
            pv = llm.prompt_version(current_topics(s, cfg.topics_file))
            nlpq.after_transcript(s, call_id, cfg.model, pv, lambda cid: send_analyze(cid, cfg.model, pv))
    except Exception as exc:                     # noqa: BLE001
        log.warning("analysis not queued for call %s: %s", call_id, type(exc).__name__)


def holder():
    global _holder
    if _holder is None:
        from .engine import ModelHolder
        cfg = queueing.AsrConfig.from_env()
        _holder = ModelHolder(cfg.model, idle_unload_s=cfg.idle_unload_s, on_unload=_gpu_free,
                              before_use=lambda: _gpu_take(cfg.idle_unload_s), diarizer_factory=_diarizer_factory())
    return _holder


def _diarizer_factory():
    """DIARIZATION=off turns the speaker split off (default on; a missing model only logs a warning)."""
    import os
    if os.getenv("DIARIZATION", "on").strip().lower() in ("off", "false", "0", "no"):
        return None
    from .diarize import Pyannote
    return Pyannote


@worker_init.connect
def _check_config(**_) -> None:
    """Refuse to start an asr worker with an unknown ASR_MODEL (it would take messages it can't do)."""
    from .engine import check_model_name
    check_model_name(queueing.AsrConfig.from_env().model)


@celery.task(name=queueing.TASK_NAME, soft_time_limit=15 * 60, time_limit=16 * 60, ignore_result=False)
def transcribe_call(call_id: int, model: str | None = None) -> dict:
    h = holder()
    model = model or h.name
    if _maint_busy():                            # backup/retention running: don't compete for disk, try in 5 min
        _park(call_id, model, MAINT_WAIT_S)
        send_transcribe(call_id, model, countdown=MAINT_WAIT_S)
        return {"call_id": call_id, "status": "queued", "maint_wait": True}
    if not _gpu_take(getattr(h, "idle_unload_s", 300)):   # an Ollama answer is running: never two models at once
        _gpu_want()                              # ... and transcription goes first once that answer is done
        _park(call_id, model, GPU_WAIT_S)
        send_transcribe(call_id, model, countdown=GPU_WAIT_S)
        return {"call_id": call_id, "status": "queued", "gpu_wait": True}
    with wiring.session_factory()() as s:
        r = process(s, call_id, model, h, wiring.settings().audio_dir)
    if not getattr(h, "loaded", True):           # nothing on the GPU (load failed): let the nlp task use it
        _gpu_free()
    if r.get("status") == "done":
        _queue_analysis(call_id)
    if r.get("retry"):
        send_transcribe(call_id, model, countdown=OOM_RETRY_S)
    elif r.get("not_ready"):
        send_transcribe(call_id, model, countdown=NOT_READY_RETRY_S)
    return r
