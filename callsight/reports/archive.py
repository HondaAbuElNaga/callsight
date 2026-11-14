"""Scheduled reports: one PDF per closed week/month in REPORTS_DIR/YYYY/, listed in `report_archive`.

Idempotent: a re-run for the same period overwrites the file (temp name, then rename) and updates the row.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .. import kpi
from ..db.models import ReportArchive
from ..ingest.classify import TeamConfig
from .pdf import filename, make_pdf

SCOPE = "all"   # team summary + one section per agent


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def generate(session: Session, kind: str, teams: TeamConfig, reports_dir: Path, period: kpi.Period | None = None,
             now: datetime | None = None) -> ReportArchive:
    """Build the PDF of `period` (default: last closed week/month), save it and upsert its archive row."""
    now = now or datetime.now(timezone.utc)
    period = period or kpi.last_closed(kind, now)
    rel = f"{period.start.year}/{filename(period)}"
    pdf = make_pdf(session, period, teams, now=now)
    write_atomic(reports_dir / rel, pdf)
    values = {"period_type": period.kind, "period_start": period.start, "period_end": period.end, "scope": SCOPE,
              "file": rel, "size_bytes": len(pdf), "generated_at": now}
    stmt = insert(ReportArchive).values(**values)
    session.execute(stmt.on_conflict_do_update(
        constraint="uq_report_archive_period",
        set_={k: stmt.excluded[k] for k in ("period_end", "file", "size_bytes", "generated_at")}))
    session.commit()
    return session.scalars(select(ReportArchive).where(
        ReportArchive.period_type == period.kind, ReportArchive.period_start == period.start,
        ReportArchive.scope == SCOPE)).one()


def catch_up(session: Session, teams: TeamConfig, reports_dir: Path, now: datetime | None = None) -> list[ReportArchive]:
    """At beat start-up: build the latest closed week and month if they are not archived yet (PC may have been off)."""
    made = []
    for kind in kpi.PERIODS:
        period = kpi.last_closed(kind, now)
        exists = session.scalar(select(ReportArchive.id).where(
            ReportArchive.period_type == kind, ReportArchive.period_start == period.start, ReportArchive.scope == SCOPE))
        if exists is None:
            made.append(generate(session, kind, teams, reports_dir, period=period, now=now))
    return made


def list_archive(session: Session, limit: int = 100) -> list[dict]:
    rows = session.scalars(select(ReportArchive).order_by(ReportArchive.period_start.desc(),
                                                          ReportArchive.period_type).limit(limit))
    return [{"id": r.id, "period_type": r.period_type, "period_start": r.period_start.isoformat(),
             "period_end": (r.period_end - timedelta(days=1)).isoformat(), "scope": r.scope, "size_bytes": r.size_bytes,
             "generated_at": r.generated_at.isoformat()} for r in rows]
