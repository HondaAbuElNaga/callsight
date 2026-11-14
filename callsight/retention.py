"""Retention (PDPL), spec 2026-11-14-hardening decision 4. OFF by default.

Runs only when RETENTION_ENABLED=true (.env) AND config/retention.yaml has `days` >= 30. The first run for a given
`days` value is always a dry run (counts only, nothing deleted); later runs delete:
  - the recording file of every call older than `days` (path from the DB row, resolved inside AUDIO_DIR; never a
    glob) and its copy in BACKUP_DIR/files/audio,
  - transcripts/analysis rows of those calls (tables that exist on this install),
then mark the call `recording_status = 'deleted'` (call metadata stays for the KPIs) and write one retention_log
row per call. Audit rows (recording plays, report downloads) are kept `audit_days` (2 years), then deleted.
Logs hold counts only. Deleted calls may still be inside DB dumps until rotation drops them (~8 weeks).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.orm import Session

from .db.models import Call, RecordingPlay, ReportDownload, RetentionLog, RetentionRun

log = logging.getLogger(__name__)

MIN_DAYS = 30
AUDIT_DAYS = 730
# per-call personal content (S2 / S5); deleted when present. blind_set stays (call ids only, keeps the test set's
# membership); a rating note may hold personal data, and without its transcript a label is useless for training.
CONTENT_TABLES = ("transcript", "call_analysis", "call_flag", "call_audio_metrics", "satisfaction_label")
BATCH = 200


@dataclass(frozen=True)
class RetentionConfig:
    enabled: bool
    days: int | None
    audit_days: int = AUDIT_DAYS

    @classmethod
    def load(cls, path: Path, env: dict | None = None) -> "RetentionConfig":
        env = os.environ if env is None else env
        data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else {}
        days = data.get("days")
        return cls(enabled=str(env.get("RETENTION_ENABLED", "false")).lower() in ("1", "true", "yes"),
                   days=int(days) if days is not None else None,
                   audit_days=max(int(data.get("audit_days") or AUDIT_DAYS), AUDIT_DAYS))

    def problem(self) -> str | None:
        if not self.enabled:
            return "retention is off (RETENTION_ENABLED is not true)"
        if self.days is None or self.days < MIN_DAYS:
            return f"retention days must be set to at least {MIN_DAYS} in config/retention.yaml"
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def select_calls(session: Session, days: int, now: datetime) -> list[Call]:
    """Calls older than `days` that still hold personal content (a recording that is not deleted yet)."""
    cutoff = now - timedelta(days=days)
    return list(session.scalars(select(Call).where(Call.start_time < cutoff,
                                                   Call.recording_status.in_(("ok", "pending", "error", "missing")))
                                .order_by(Call.id)))


def _inside(root: Path, rel: str | None) -> Path | None:
    if not rel:
        return None
    base = root.resolve()
    p = (base / rel).resolve()
    return p if p.is_relative_to(base) and p != base else None


def run(session: Session, cfg: RetentionConfig, audio_dir: Path, backup_files_dir: Path | None,
        now: datetime | None = None, force_dry_run: bool = False) -> dict:
    now = now or _utcnow()
    why = cfg.problem()
    if why:
        return {"enabled": False, "reason": why}
    days = cfg.days
    had_dry_run = session.scalar(select(RetentionRun.id).where(RetentionRun.dry_run.is_(True),
                                                               RetentionRun.days == days).limit(1)) is not None
    dry = force_dry_run or not had_dry_run
    calls = select_calls(session, days, now)
    audit_cutoff = now - timedelta(days=cfg.audit_days)
    audit_where = [(RecordingPlay, RecordingPlay.played_at < audit_cutoff),
                   (ReportDownload, ReportDownload.downloaded_at < audit_cutoff)]
    audit_rows = sum(session.scalar(select(func.count()).select_from(m).where(w)) or 0 for m, w in audit_where)
    files = 0
    r = RetentionRun(ran_at=now, dry_run=dry, days=days, calls=len(calls), files=0, audit_rows=audit_rows)
    session.add(r)
    session.flush()
    if dry:
        files = sum(1 for c in calls if (p := _inside(audio_dir, c.recording_path)) and p.is_file())
        r.files = files
        session.commit()
        log.info("retention DRY RUN (%s days): %s calls, %s recording files, %s audit rows would be deleted",
                 days, len(calls), files, audit_rows)
        return {"enabled": True, "dry_run": True, "days": days, "calls": len(calls), "files": files,
                "audit_rows": audit_rows}

    present = [t for t in CONTENT_TABLES if inspect(session.get_bind()).has_table(t)]
    from .backup import MANIFEST, read_manifest, write_manifest
    manifest_path = backup_files_dir / MANIFEST if backup_files_dir else None
    manifest = read_manifest(manifest_path) if manifest_path else {}
    pruned = False
    for i in range(0, len(calls), BATCH):
        for c in calls[i:i + BATCH]:
            what = []
            for root, label in ((audio_dir, "recording"), (backup_files_dir and backup_files_dir / "audio", "backup_copy")):
                p = _inside(root, c.recording_path) if root else None
                if p and p.is_file():
                    p.unlink()
                    files += 1
                    what.append(label)
                if p and label == "backup_copy":   # drop its manifest line too (PDPL: the name may hold a number)
                    key = "audio/" + p.relative_to(root.resolve()).as_posix()
                    pruned = manifest.pop(key, None) is not None or pruned
            for t in present:
                if session.execute(text(f'DELETE FROM "{t}" WHERE call_id = :id'), {"id": c.id}).rowcount:
                    what.append(t)
            c.recording_status, c.recording_path = "deleted", None
            session.add(RetentionLog(run_id=r.id, call_id=c.id, deleted_at=now, what=",".join(what) or "nothing"))
        session.commit()
        if pruned:
            write_manifest(manifest_path, manifest)
            pruned = False
    for model, where in audit_where:
        session.execute(delete(model).where(where))
    r.files = files
    session.commit()
    log.info("retention (%s days): %s calls cleared, %s files deleted, %s audit rows deleted",
             days, len(calls), files, audit_rows)
    return {"enabled": True, "dry_run": False, "days": days, "calls": len(calls), "files": files,
            "audit_rows": audit_rows}
