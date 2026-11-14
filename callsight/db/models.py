"""Database tables (SQLAlchemy 2). Schema changes go through Alembic migrations."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


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
    recording_file: Mapped[str | None] = mapped_column(String(300))
    recording_path: Mapped[str | None] = mapped_column(String(400))   # relative to AUDIO_DIR
    recording_status: Mapped[str] = mapped_column(String(10), default="none")  # none/pending/ok/error/missing
    recording_attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    legs: Mapped[list["CallLeg"]] = relationship(back_populates="call", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_call_start_time", "start_time"),
        Index("ix_call_agent_ext", "agent_ext"),
        Index("ix_call_status", "status"),
        Index("ix_call_recording_status", "recording_status"),
    )


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
    role: Mapped[str] = mapped_column(String(20))                 # admin / supervisor
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
    segmenter_version: Mapped[str | None] = mapped_column(String(30))
    diarization_version: Mapped[str | None] = mapped_column(String(30))   # NULL until speaker split exists
    error: Mapped[str | None] = mapped_column(String(300))        # ids/reasons only, never transcript text
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # never rewritten by retries/reaper/--force: decides "live call" for the latency figure
    first_queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    segments: Mapped[list["TranscriptSegment"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan", order_by="TranscriptSegment.seq")

    __table_args__ = (
        UniqueConstraint("call_id", "model", name="uq_transcript_call_model"),
        Index("ix_transcript_status", "status"),
    )


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
