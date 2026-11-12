"""Database tables (SQLAlchemy 2). Schema changes go through Alembic migrations."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text,
                        func)
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
