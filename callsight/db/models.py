"""Database tables (SQLAlchemy 2). Schema changes go through Alembic migrations."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (DDL, BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer,
                        SmallInteger, String, Text, UniqueConstraint, event, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def norm_number(col):
    """Customer number for matching repeat calls: digits only, last 9 (strips +966 / 00966 / leading 0)."""
    return func.right(func.regexp_replace(col, "[^0-9]", "", "g"), 9)


class Agent(Base):
    __tablename__ = "agent"
    ext: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    team: Mapped[str | None] = mapped_column(String(50))
    in_team: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class Queue(Base):
    __tablename__ = "queue"
    number: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    team: Mapped[str | None] = mapped_column(String(50))


class Call(Base):
    __tablename__ = "call"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(100), unique=True)
    direction: Mapped[str] = mapped_column(String(10))
    entry: Mapped[str] = mapped_column(String(10))
    customer_number: Mapped[str | None] = mapped_column(String(40))
    did_number: Mapped[str | None] = mapped_column(String(40))
    queue_number: Mapped[str | None] = mapped_column(String(16))
    team: Mapped[str | None] = mapped_column(String(50))
    agent_ext: Mapped[str | None] = mapped_column(String(16))
    missed_agent_ext: Mapped[str | None] = mapped_column(String(16))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ivr_seconds: Mapped[int] = mapped_column(Integer, default=0)
    wait_seconds: Mapped[int] = mapped_column(Integer, default=0)
    talk_seconds: Mapped[int] = mapped_column(Integer, default=0)
    hold_seconds: Mapped[int | None] = mapped_column(Integer)
    total_seconds: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20))
    hangup_by: Mapped[str | None] = mapped_column(String(10))   # outbound: agent / customer / busy (ring-time spec)
    recording_file: Mapped[str | None] = mapped_column(String(300))
    recording_path: Mapped[str | None] = mapped_column(String(400))   # relative to AUDIO_DIR
    recording_status: Mapped[str] = mapped_column(String(10), default="none")  # none/pending/ok/error/missing/deleted/held
    recording_attempts: Mapped[int] = mapped_column(Integer, default=0)
    # agent-supervisor spec: an admin erased this call's personal content (recording, texts, forms); row kept for KPIs
    content_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    legs: Mapped[list["CallLeg"]] = relationship(back_populates="call", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_call_start_time", "start_time"),
        Index("ix_call_agent_ext", "agent_ext"),
        Index("ix_call_status", "status"),
        Index("ix_call_recording_status", "recording_status"),
        Index("ix_call_customer_start", "customer_number", "start_time"),
    )


Index("ix_call_norm_number", norm_number(Call.customer_number), Call.start_time)   # repeat-call look-back


class CallLeg(Base):
    __tablename__ = "call_leg"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)  # PBX CDR id
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), index=True)
    uid: Mapped[str] = mapped_column(String(100), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    from_number: Mapped[str | None] = mapped_column(String(40))
    to_number: Mapped[str | None] = mapped_column(String(40))
    disposition: Mapped[str | None] = mapped_column(String(20))
    duration: Mapped[int | None] = mapped_column(Integer)
    ring_duration: Mapped[int | None] = mapped_column(Integer)
    talk_duration: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(300))
    record_file: Mapped[str | None] = mapped_column(String(300))
    raw: Mapped[dict] = mapped_column(JSONB)

    call: Mapped[Call] = relationship(back_populates="legs")


class IngestState(Base):
    __tablename__ = "ingest_state"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class ReconcileRun(Base):
    __tablename__ = "reconcile_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    pbx_team_calls: Mapped[int] = mapped_column(Integer)
    pbx_team_legs: Mapped[int] = mapped_column(Integer)
    db_team_calls: Mapped[int] = mapped_column(Integer)
    missing_legs_before: Mapped[int] = mapped_column(Integer)
    missing_legs_after: Mapped[int] = mapped_column(Integer)
    recordings_expected: Mapped[int] = mapped_column(Integer)
    missing_recordings_before: Mapped[int] = mapped_column(Integer)
    missing_recordings_after: Mapped[int] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean)


class AppUser(Base):
    __tablename__ = "app_user"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))                 # admin / supervisor / agent
    ext: Mapped[str | None] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)  # +1 on password change/disable -> old logins end
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecordingPlay(Base):
    """Audit: who played which recording (recordings are personal data)."""
    __tablename__ = "recording_play"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), index=True)
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GoldClip(Base):
    """A 60 s piece of a real call with the text typed by a person (ASR gold set)."""
    __tablename__ = "gold_clip"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), unique=True)
    start_seconds: Mapped[float] = mapped_column(Float)
    seconds: Mapped[float] = mapped_column(Float)
    file: Mapped[str] = mapped_column(String(300))          # relative to AUDIO_DIR
    reference_text: Mapped[str | None] = mapped_column(Text)
    typed_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    typed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReportArchive(Base):
    """A generated weekly/monthly PDF (scheduled job). Re-running a period replaces the row and the file."""
    __tablename__ = "report_archive"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period_type: Mapped[str] = mapped_column(String(10))           # week / month
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)                  # exclusive
    scope: Mapped[str] = mapped_column(String(60))                  # "all" (team summary + every agent)
    file: Mapped[str] = mapped_column(String(300))                  # relative to REPORTS_DIR
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("period_type", "period_start", "scope", name="uq_report_archive_period"),)


class ReportDownload(Base):
    """Audit: who downloaded which report (reports contain personal performance data)."""
    __tablename__ = "report_download"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    archive_id: Mapped[int | None] = mapped_column(ForeignKey("report_archive.id", ondelete="SET NULL"))
    period_type: Mapped[str] = mapped_column(String(10))
    period_start: Mapped[date] = mapped_column(Date)
    scope: Mapped[str] = mapped_column(String(60))
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- hardening (spec 2026-11-14-hardening) ----------

class BackupRun(Base):
    """One `python -m callsight backup` run. `counts` = rows per table in the dump's snapshot (restore drill check)."""
    __tablename__ = "backup_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="running")       # running / ok / error
    dump_file: Mapped[str | None] = mapped_column(String(200))              # name inside BACKUP_DIR/db
    dump_bytes: Mapped[int | None] = mapped_column(BigInteger)
    counts: Mapped[dict | None] = mapped_column(JSONB)
    alembic_version: Mapped[str | None] = mapped_column(String(50))
    files_copied: Mapped[int] = mapped_column(Integer, default=0)
    files_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    error: Mapped[str | None] = mapped_column(String(300))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verify_ok: Mapped[bool | None] = mapped_column(Boolean)


class JobRun(Base):
    """Last start / success / failure of each scheduled job (health page)."""
    __tablename__ = "job_run"
    job: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(10))               # ok / error


class RetentionRun(Base):
    """One retention run (PDPL). The first run for a given number of days is always a dry run."""
    __tablename__ = "retention_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dry_run: Mapped[bool] = mapped_column(Boolean)
    days: Mapped[int] = mapped_column(Integer)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    files: Mapped[int] = mapped_column(Integer, default=0)
    audit_rows: Mapped[int] = mapped_column(Integer, default=0)


class RetentionLog(Base):
    """Audit: one row per call whose recording/transcripts were deleted by retention (call metadata is kept)."""
    __tablename__ = "retention_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("retention_run.id"), index=True)
    call_id: Mapped[int] = mapped_column(BigInteger, index=True)           # no FK: the audit outlives the call
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    what: Mapped[str] = mapped_column(String(100))                         # e.g. "recording,backup_copy,transcripts"


class Transcript(Base):
    """Speech-to-text of one call by one model (spec 2026-11-14-transcription). Text = personal data (PDPL)."""
    __tablename__ = "transcript"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"))
    model: Mapped[str] = mapped_column(String(50))
    model_revision: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10))               # queued/running/done/error/skipped
    text: Mapped[str | None] = mapped_column(Text)
    audio_seconds: Mapped[float | None] = mapped_column(Float)
    speech_seconds: Mapped[float | None] = mapped_column(Float)    # windows that kept text
    compute_seconds: Mapped[float | None] = mapped_column(Float)
    dropped_windows: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    redo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # --force/--retry-errors:
    # left out of the live latency figure (its finish time says nothing about hang-up -> transcript)
    segmenter_version: Mapped[str | None] = mapped_column(String(30))
    diarization_version: Mapped[str | None] = mapped_column(String(30))   # NULL until speaker split exists
    error: Mapped[str | None] = mapped_column(String(300))        # ids/reasons only, never transcript text
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # never rewritten by retries/reaper/--force: decides "live call" for the latency figure
    first_queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # transcript-view spec: kept segment texts after search_normalize() (callsight/transcripts.py); NULL until done
    search_text: Mapped[str | None] = mapped_column(Text)

    segments: Mapped[list["TranscriptSegment"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan", order_by="TranscriptSegment.seq")

    __table_args__ = (
        UniqueConstraint("call_id", "model", name="uq_transcript_call_model"),
        Index("ix_transcript_status", "status"),
        Index("ix_transcript_search_trgm", "search_text", postgresql_using="gin",
              postgresql_ops={"search_text": "gin_trgm_ops"}),
    )


# The trigram index needs pg_trgm; create_all (tests) makes it first. Migration 0007 does the same.
event.listen(Transcript.__table__, "before_create", DDL("CREATE EXTENSION IF NOT EXISTS pg_trgm"))


class TranscriptSegment(Base):
    """One audio window of a transcript. `flag` = why its text was dropped (silence/blocklist/repeat/too_fast)."""
    __tablename__ = "transcript_segment"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transcript_id: Mapped[int] = mapped_column(ForeignKey("transcript.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)                      # order in the call, 0-based
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    speaker: Mapped[str | None] = mapped_column(String(20))        # NULL until diarization
    text: Mapped[str] = mapped_column(Text, default="")
    flag: Mapped[str | None] = mapped_column(String(20))

    transcript: Mapped[Transcript] = relationship(back_populates="segments")

    __table_args__ = (UniqueConstraint("transcript_id", "seq", name="uq_transcript_segment_seq"),)


class TranscriptAccess(Base):
    """Audit: who opened (view) or downloaded (export) which call's transcript. Never holds transcript text."""
    __tablename__ = "transcript_access"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))                  # view / export
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- call analysis (spec 2026-11-14-call-analysis). Every row goes with its call (ON DELETE CASCADE). ----------

class CallAnalysis(Base):
    """Local-LLM output for one call, per (model, prompt_version). Summary/reason = personal data (PDPL): never logged.
    `llm_score` is a first, unvalidated estimate ("تقدير أولي"): never in KPIs/PDFs, never per agent (decision 9)."""
    __tablename__ = "call_analysis"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"))
    transcript_id: Mapped[int | None] = mapped_column(BigInteger)   # which transcript was read (no FK: goes with call)
    model: Mapped[str] = mapped_column(String(50))                # e.g. qwen2.5:7b
    prompt_version: Mapped[str] = mapped_column(String(30))       # prompt id + hash of topics.yaml
    status: Mapped[str] = mapped_column(String(10))               # queued/running/done/error/skipped
    summary: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(30))         # stable key from config/topics.yaml
    action_items: Mapped[list | None] = mapped_column(JSONB)
    llm_score: Mapped[int | None] = mapped_column(Integer)        # 1-10
    reason: Mapped[str | None] = mapped_column(Text)
    escalation: Mapped[bool | None] = mapped_column(Boolean)
    trimmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # head+tail cut
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    compute_seconds: Mapped[float | None] = mapped_column(Float)
    tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(String(300))        # ids/reasons only, never text
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("call_id", "model", "prompt_version", name="uq_call_analysis_version"),
        Index("ix_call_analysis_status", "status"),
        Index("ix_call_analysis_call_id", "call_id"),
    )


class CallFlag(Base):
    """A rules.yaml phrase found in a kept transcript window (no LLM). Always "needs listening"; never in KPIs."""
    __tablename__ = "call_flag"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), index=True)
    transcript_id: Mapped[int] = mapped_column(BigInteger)          # no FK: the row goes with its call
    kind: Mapped[str] = mapped_column(String(20))                 # bad_word / escalation / competitor
    phrase: Mapped[str] = mapped_column(String(100))              # the rules.yaml phrase or competitor_rule label (not transcript text)
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    rules_version: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CallAudioMetrics(Base):
    """Silence metrics from the recording (RMS frames, no diarization). Hold music is not silence."""
    __tablename__ = "call_audio_metrics"
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), primary_key=True)
    metrics_version: Mapped[str] = mapped_column(String(30))
    audio_seconds: Mapped[float] = mapped_column(Float)
    silence_seconds: Mapped[float] = mapped_column(Float)
    silence_share: Mapped[float] = mapped_column(Float)            # 0..1
    longest_silence_s: Mapped[float] = mapped_column(Float)
    silences: Mapped[int] = mapped_column(Integer)                 # silences >= min_silence_s
    long_silences: Mapped[int] = mapped_column(Integer)            # silences > long_silence_s (hold-like)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SatisfactionLabel(Base):
    """A supervisor's 1-10 rating of the CUSTOMER's satisfaction in one call (training/test data, not HR data)."""
    __tablename__ = "satisfaction_label"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    score: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(500))
    blind: Mapped[bool] = mapped_column(Boolean)                   # call was in the blind set when rated
    prefill: Mapped[int | None] = mapped_column(Integer)           # LLM estimate shown as pre-fill (None = none)
    changed_from_prefill: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("call_id", "user_id", name="uq_satisfaction_label_call_user"),
        CheckConstraint("score BETWEEN 1 AND 10", name="ck_satisfaction_label_score"),
    )


class BlindSet(Base):
    """The frozen 100-call test set (decision 8): chosen once, before any LLM estimate is shown; never trained on."""
    __tablename__ = "blind_set"
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer)
    agent_ext: Mapped[str | None] = mapped_column(String(16))
    sampler_version: Mapped[str] = mapped_column(String(20))
    salt: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RatingPick(Base):
    """The rating set (spec 2026-11-16-rating-set): calls of days the user chose, picked once, rated first (after the
    blind ones); the blind set is drawn from these when they exist."""
    __tablename__ = "rating_pick"
    call_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("call.id", ondelete="CASCADE"), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer)
    day: Mapped[date] = mapped_column(Date)
    agent_ext: Mapped[str | None] = mapped_column(String(16))
    salt: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ListenAudit(Base):
    """Append-only audit of every Listen attempt (spec live-board, decision 4), including refused ones.
    kind 'attempt' (result pending / denied_*) is written BEFORE any PBX call; kind 'result' (ok / refused / error)
    after it, pointing at its attempt. Database triggers refuse UPDATE, DELETE and TRUNCATE of a non-empty table
    (bypass only with SET LOCAL callsight.audit_maintenance = 'on'). They stop mistakes, not the DB owner: real
    tamper resistance needs an INSERT/SELECT-only DB role for the API (later idea). client_ip is the Docker bridge
    gateway behind the port mapping, not the user's PC: user_id identifies who."""
    __tablename__ = "listen_audit"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(10))                  # attempt / result
    result: Mapped[str] = mapped_column(String(20))
    attempt_id: Mapped[int | None] = mapped_column(ForeignKey("listen_audit.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    user_ext: Mapped[str | None] = mapped_column(String(20))
    client_ip: Mapped[str | None] = mapped_column(String(64))
    live_call_id: Mapped[str] = mapped_column(String(64))         # PBX call id (live calls are not in `call` yet)
    agent_ext: Mapped[str | None] = mapped_column(String(20))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


LISTEN_AUDIT_APPEND_ONLY = (
    "CREATE OR REPLACE FUNCTION listen_audit_append_only() RETURNS trigger AS $$ BEGIN "
    "RAISE EXCEPTION 'listen_audit is append-only'; END; $$ LANGUAGE plpgsql",
    "CREATE TRIGGER listen_audit_no_change BEFORE UPDATE OR DELETE ON listen_audit "
    "FOR EACH ROW EXECUTE FUNCTION listen_audit_append_only()",
    "CREATE OR REPLACE FUNCTION listen_audit_no_truncate() RETURNS trigger AS $$ BEGIN "
    "IF EXISTS (SELECT 1 FROM listen_audit) AND "
    "coalesce(current_setting('callsight.audit_maintenance', true), '') <> 'on' THEN "
    "RAISE EXCEPTION 'listen_audit is append-only (TRUNCATE refused)'; END IF; RETURN NULL; END; $$ "
    "LANGUAGE plpgsql",
    "CREATE TRIGGER listen_audit_no_truncate BEFORE TRUNCATE ON listen_audit "
    "FOR EACH STATEMENT EXECUTE FUNCTION listen_audit_no_truncate()",
)
for _sql in LISTEN_AUDIT_APPEND_ONLY:      # also for create_all (tests); migration 0009 runs the same statements
    event.listen(ListenAudit.__table__, "after_create", DDL(_sql).execute_if(dialect="postgresql"))


# ---------- agent + supervisor work (spec 2026-11-15-agent-supervisor) ----------

class CallFollowup(Base):
    """The agent's short form after a needed call (personal data: deleted with the call's content).
    responsible_kind: user (responsible_user_id) / customer / other_department. Callback status is computed on read
    (callsight/followups.py), never stored."""
    __tablename__ = "call_followup"
    __table_args__ = (CheckConstraint("priority IN ('high', 'medium', 'low') OR priority IS NULL",
                                      name="ck_call_followup_priority"),
                      CheckConstraint("forward_status IN ('pending', 'sent', 'failed') OR forward_status IS NULL",
                                      name="ck_call_followup_forward_status"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), unique=True)
    owner_ext: Mapped[str] = mapped_column(String(16), index=True)           # call.agent_ext when filled
    no_followup: Mapped[bool] = mapped_column(Boolean, default=False)
    what_happened: Mapped[str | None] = mapped_column(String(500))
    solved: Mapped[bool | None] = mapped_column(Boolean)
    # spec 2026-11-16-priority-no-followup: high / medium / low, set in the first form only (NULL = old form or
    # no_followup). Fixed English codes: another system will read them.
    priority: Mapped[str | None] = mapped_column(String(10))
    responsible_kind: Mapped[str | None] = mapped_column(String(20))
    responsible_user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    expected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    callback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filled_by: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    first_filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # spec 2026-11-16-priority-no-followup part 4 (D1): responsible_kind "forward" = a branch supervisor in the
    # customer-service system. Status: pending (to send / retrying) -> sent (ticket made) / failed (error shown).
    forward_person_id: Mapped[int | None] = mapped_column(ForeignKey("forward_person.id"))
    forward_note: Mapped[str | None] = mapped_column(String(500))
    forward_status: Mapped[str | None] = mapped_column(String(10))
    forward_ticket_guid: Mapped[str | None] = mapped_column(String(64))
    forward_ticket_number: Mapped[int | None] = mapped_column(Integer)
    forwarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # the ticket was made
    forward_error: Mapped[str | None] = mapped_column(String(300))
    forward_tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # D2: read back from the ticket every few minutes (closedAt there; NULL again when they reopen it)
    forward_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forward_solution: Mapped[str | None] = mapped_column(String(1000))      # text of the last followup_close
    forward_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ForwardEvent(Base):
    """One item of a forwarded ticket's timeline in the customer-service system (part 4 D2): events (created,
    forwarded, reopened, closed...) and followups (the supervisor's questions / replies). ext_key = hash of the item,
    so a re-read never adds it twice. Personal data: erased with the call's content."""
    __tablename__ = "forward_event"
    __table_args__ = (UniqueConstraint("call_id", "ext_key", name="uq_forward_event_call_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), index=True)
    ext_key: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))        # event / followup
    type: Mapped[str] = mapped_column(String(30))        # created / forwarded / followup / followup_close / ...
    note: Mapped[str | None] = mapped_column(String(1000))
    at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str | None] = mapped_column(String(200))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))       # when we first read it
    # the hop (person) that held the ticket when it was first read (forward chain story page)
    hop_id: Mapped[int | None] = mapped_column(ForeignKey("forward_hop.id", ondelete="SET NULL"))


class ForwardPerson(Base):
    """A branch supervisor who can receive a forwarded problem (config/forward_people.yaml, part 4 D1). guid = their
    id in the customer-service system (from the yaml, or matched by name on sync); ext = PBX extension (proof calls).
    active = still in the yaml and, after a sync, still a supervisor in that system. Not personal data of a call."""
    __tablename__ = "forward_person"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    branch: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    ext: Mapped[str | None] = mapped_column(String(16))
    guid: Mapped[str | None] = mapped_column(String(64), unique=True)
    full_name: Mapped[str | None] = mapped_column(String(200))     # as the other system writes it (after a sync)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # the forward chain: every employee of the other system is stored (any can be chosen); level 2 = his branch's
    # supervisor (isBranchSupervisor there), level 3 = the managers listed in the yaml (`managers:`)
    is_supervisor: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_manager: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    grp: Mapped[str | None] = mapped_column(String(20))   # the other system's group (men / women): branch names repeat


class ForwardHop(Base):
    """One person who held a forwarded problem (spec "Part 4 change: the forward chain"): level 1 the chosen
    employee, 2 his branch supervisor, 3 a manager (all the managers get it: the first on the same ticket, the others a
    copy ticket each). result: pending (copy ticket not made yet) / open / solved / passed_on (24 h ran out) /
    closed_by_other (another manager solved it) / failed. Levels 1 and 2 share the first ticket. The solution text
    is personal data: erased with the call's content."""
    __tablename__ = "forward_hop"
    __table_args__ = (CheckConstraint("level IN (1, 2, 3)", name="ck_forward_hop_level"),
                      CheckConstraint("result IN ('pending', 'open', 'solved', 'passed_on', 'closed_by_other', "
                                      "'failed')", name="ck_forward_hop_result"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), index=True)
    level: Mapped[int] = mapped_column(SmallInteger)
    person_id: Mapped[int] = mapped_column(ForeignKey("forward_person.id"))
    ticket_guid: Mapped[str | None] = mapped_column(String(64))
    ticket_number: Mapped[int | None] = mapped_column(Integer)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))     # when this person got it
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # closedAt there (None = open)
    solution: Mapped[str | None] = mapped_column(String(1000))
    result: Mapped[str] = mapped_column(String(20), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))    # solved / passed on / closed
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(300))
    tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class CallFollowupNote(Base):
    """A note written after a callback (spec 2026-11-15-followup-stages): round >= 2. Append-only. cb_call_id = the
    callback call it reports on. Not solved -> a new callback time (the next round). Personal data: erased with the
    call's content."""
    __tablename__ = "call_followup_note"
    __table_args__ = (UniqueConstraint("call_id", "round", name="uq_call_followup_note_round"),
                      CheckConstraint("round >= 2", name="ck_call_followup_note_round"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), index=True)
    round: Mapped[int] = mapped_column(Integer)
    cb_call_id: Mapped[int | None] = mapped_column(ForeignKey("call.id", ondelete="SET NULL"))
    what_happened: Mapped[str] = mapped_column(String(500))
    solved: Mapped[bool] = mapped_column(Boolean)
    responsible_kind: Mapped[str | None] = mapped_column(String(20))
    responsible_user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    expected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    callback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CallPrivateNote(Base):
    """A user's own reminder on a call (private-note spec): only the writer ever sees it. Erased with the content."""
    __tablename__ = "call_private_note"
    __table_args__ = (UniqueConstraint("call_id", "user_id", name="uq_call_private_note"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(String(300))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CallShare(Base):
    """A supervisor sends one call with a note to one agent. The agent sees the note only after clicking it (that
    click is `seen_at`, shown to the supervisor) and may answer with one reply (changeable)."""
    __tablename__ = "call_share"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"), index=True)
    from_user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    to_user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    note: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply: Mapped[str | None] = mapped_column(String(1000))
    reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppSetting(Base):
    """Settings the admin changes on the web page (followup_start, followup_kpi_since: ISO dates or absent)."""
    __tablename__ = "app_setting"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdminLog(Base):
    """Who changed users / settings / shares or erased a call's content. Never holds passwords or call text.
    target has no FK: the row outlives the call or user it names."""
    __tablename__ = "admin_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(String(300))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
