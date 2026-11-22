"""Retention (PDPL), spec 2026-11-14-hardening decision 4. OFF by default.

Runs only when RETENTION_ENABLED=true (.env) AND config/retention.yaml has `days` >= 30. The first run for a given
`days` value is always a dry run (counts only, nothing deleted); later runs delete:
  - the recording file of every call older than `days` (path from the DB row, resolved inside AUDIO_DIR; never a
    glob) and its copy in BACKUP_DIR/files/audio,
  - transcripts/analysis rows of those calls (tables that exist on this install),
then mark the call `recording_status = 'deleted'` (call metadata stays for the KPIs) and write one retention_log
row per call. Audit rows (recording plays, report downloads) are kept `audit_days` (2 years), then deleted.
Logs hold counts only. Deleted calls may still be inside DB dumps until rotation drops them (~8 weeks).

Kept (user 2026-11-22): while `keep_rating_set` is true, the rating work (rating set, blind set, rated calls) and
the 30 first-speaker-check calls; calls with open work (a follow-up not solved / not filled, an open forwarded
ticket, a shared call the agent has not closed) until closed, at most `open_max_days`. Before erasing, the KPI
numbers of every closed week / month touched are saved (kpi_snapshot), so old reports keep their figures.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import and_, delete, exists, false, func, inspect, not_, or_, select, text, union
from sqlalchemy.orm import Session

from .db.models import (BlindSet, Call, CallFollowup, CallFollowupNote, CallShare, ForwardHop, GoldClip, RatingPick,
                         RecordingPlay, ReportDownload, RetentionLog, RetentionRun, SatisfactionLabel)

log = logging.getLogger(__name__)

MIN_DAYS = 30
AUDIT_DAYS = 730
OPEN_MAX_DAYS = 180                      # open work keeps its call at most this long (user 2026-11-22)
DRY_RUN_VALID_DAYS = 7                   # before the first real run, the dry run must be this recent
# per-call personal content (S2 / S5); deleted when present. blind_set stays (call ids only, keeps the test set's
# membership); a rating note may hold personal data, and without its transcript a label is useless for training.
CONTENT_TABLES = ("transcript", "call_analysis", "call_flag", "call_audio_metrics", "satisfaction_label",
                  "call_followup", "call_followup_note", "call_share",
                  "call_private_note",   # follow-up text + notes are personal too
                  "forward_message",     # what we wrote on the ticket: answers, notes, AI summary (D3)
                  "forward_event",       # the forwarded ticket's timeline (part 4 D2)
                  "forward_hop")         # who held it + the solution text (forward chain)
BATCH = 200


@dataclass(frozen=True)
class RetentionConfig:
    enabled: bool
    days: int | None
    audit_days: int = AUDIT_DAYS
    keep_rating_set: bool = True         # rating work + first-speaker check kept until the model is trained
    open_max_days: int = OPEN_MAX_DAYS

    @classmethod
    def load(cls, path: Path, env: dict | None = None) -> "RetentionConfig":
        env = os.environ if env is None else env
        data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else {}
        days = data.get("days")
        days = int(days) if days is not None else None
        keep = data.get("keep_rating_set")
        return cls(enabled=str(env.get("RETENTION_ENABLED", "false")).lower() in ("1", "true", "yes"),
                   days=days,
                   audit_days=max(int(data.get("audit_days") or AUDIT_DAYS), AUDIT_DAYS),
                   keep_rating_set=True if keep is None else bool(keep),       # missing = keep (the safe side)
                   open_max_days=max(int(data.get("open_max_days") or OPEN_MAX_DAYS), days or 0))

    def problem(self) -> str | None:
        if not self.enabled:
            return "retention is off (RETENTION_ENABLED is not true)"
        if self.days is None or self.days < MIN_DAYS:
            return f"retention days must be set to at least {MIN_DAYS} in config/retention.yaml"
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def rating_work():
    """Call ids kept while keep_rating_set is on: rating set, blind test set, every rated call, the 30 check calls."""
    return union(select(RatingPick.call_id), select(BlindSet.call_id), select(SatisfactionLabel.call_id),
                 select(GoldClip.call_id))


def open_work(session: Session, teams=None):
    """SQL condition on Call: the call still has open work (the follow-up page's open list, kept simple and on the
    safe side): a follow-up whose latest entry is not "solved" and whose forward chain is not solved, a forward
    still sending / failed, an open forwarded ticket, a shared call the agent has not closed, or (with `teams`) a
    needed follow-up form never filled."""
    from . import followups as fu
    last_note = (select(CallFollowupNote.solved).where(CallFollowupNote.call_id == CallFollowup.call_id)
                 .order_by(CallFollowupNote.round.desc()).limit(1).correlate(CallFollowup).scalar_subquery())
    hop_solved = exists().where(ForwardHop.call_id == CallFollowup.call_id, ForwardHop.result == "solved")
    form_open = exists().where(CallFollowup.call_id == Call.id, CallFollowup.no_followup.is_(False),
                               or_(func.coalesce(last_note, CallFollowup.solved).is_not(True),
                                   CallFollowup.forward_status.in_(("pending", "failed"))),
                               ~hop_solved)
    conds = [form_open,
             exists().where(ForwardHop.call_id == Call.id, ForwardHop.result.in_(("open", "pending"))),
             exists().where(CallShare.call_id == Call.id, CallShare.agent_closed_at.is_(None))]
    start = fu.get_setting(session, fu.SETTING_START)
    if teams is not None and start is not None and teams.ext_team:
        conds.append(and_(*fu.needed_conditions(sorted(teams.ext_team), start),
                          ~exists().where(CallFollowup.call_id == Call.id)))
    return or_(*conds)


def _candidates(days: int, now: datetime):
    """Calls older than `days` that still hold personal content (a recording that is not deleted yet)."""
    return and_(Call.start_time < now - timedelta(days=days),
                Call.recording_status.in_(("ok", "pending", "error", "missing", "held")))


def _keep(session: Session, keep_rating_set: bool, open_max_days: int, now: datetime, teams=None):
    """(kept for rating work, kept for open work) conditions on Call."""
    rating = Call.id.in_(rating_work()) if keep_rating_set else false()
    open_ = and_(Call.start_time >= now - timedelta(days=open_max_days), open_work(session, teams))
    return rating, open_


def _linked_to_kept(session: Session, kept) -> set[int]:
    """The branch supervisors' calls (the proof of a forwarded problem) of kept calls: kept with them, or the
    forward chain of a call kept for open work could never be confirmed."""
    from .forward import linked_calls
    with_chain = select(Call).where(kept, exists().where(ForwardHop.call_id == Call.id, ForwardHop.level == 2))
    return {x.id for c in session.scalars(with_chain) for x in linked_calls(session, c)}


def select_calls(session: Session, days: int, now: datetime, *, keep_rating_set: bool = True,
                 open_max_days: int = OPEN_MAX_DAYS, teams=None) -> list[Call]:
    """Calls to erase: older than `days`, still holding content, not kept (rating work / open work, and the
    branch supervisors' proof calls of those)."""
    rating, open_ = _keep(session, keep_rating_set, open_max_days, now, teams)
    stmt = select(Call).where(_candidates(days, now), not_(rating), not_(open_))
    linked = _linked_to_kept(session, and_(_candidates(days, now), or_(rating, open_)))
    if linked:
        stmt = stmt.where(Call.id.not_in(linked))
    return list(session.scalars(stmt.order_by(Call.id)))


def snapshot_days(session: Session, calls: list[Call]) -> set:
    """Riyadh days whose week / month KPI numbers must be saved before these calls' content is erased: the calls'
    own days, and the days their shared-call notes were sent (shares count by the day sent, maybe a later week)."""
    from .config import RIYADH
    days = {c.start_time.astimezone(RIYADH).date() for c in calls}
    ids = [c.id for c in calls]
    for i in range(0, len(ids), 1000):
        days |= {t.astimezone(RIYADH).date() for t in session.scalars(
            select(CallShare.created_at).where(CallShare.call_id.in_(ids[i:i + 1000])))}
    return days


def drop_draft(path: Path, name: str) -> None:
    """The gold set's machine drafts file holds transcript text per clip: remove the erased clip's entry."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(data, dict) and data.pop(name, None) is not None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def erase_gold_clips(session: Session, call_id: int, audio_dir: Path) -> int:
    """The 60 s gold clip of a call (file + typed reference text + machine draft). Returns files deleted."""
    n = 0
    for clip in session.scalars(select(GoldClip).where(GoldClip.call_id == call_id)):
        p = _inside(audio_dir, clip.file)
        if p and p.is_file():
            p.unlink()
            n += 1
        drop_draft(audio_dir / "goldset" / "drafts.json", Path(clip.file).name)
        session.delete(clip)
    return n


def kept_counts(session: Session, days: int, now: datetime, *, keep_rating_set: bool = True,
                open_max_days: int = OPEN_MAX_DAYS, teams=None) -> tuple[int, int]:
    """How many old calls are kept: (rating work, open work not already counted as rating work)."""
    rating, open_ = _keep(session, keep_rating_set, open_max_days, now, teams)

    def n(*where) -> int:
        return session.scalar(select(func.count()).select_from(Call).where(_candidates(days, now), *where)) or 0
    return n(rating), n(not_(rating), open_)


def _inside(root: Path, rel: str | None) -> Path | None:
    if not rel:
        return None
    base = root.resolve()
    p = (base / rel).resolve()
    return p if p.is_relative_to(base) and p != base else None


def erase_call_content(session: Session, c: Call, audio_dir: Path, backup_files_dir: Path | None,
                       manifest: dict | None, tables=None) -> tuple[list[str], int, bool]:
    """Delete one call's recording file (+ its backup copy when backup_files_dir is given) and its content rows;
    mark the call deleted (the row stays for the KPIs). Returns (what, files deleted, manifest changed). No commit."""
    tables = [t for t in CONTENT_TABLES if inspect(session.get_bind()).has_table(t)] if tables is None else tables
    what: list[str] = []
    files, pruned = 0, False
    for root, label in ((audio_dir, "recording"), (backup_files_dir and backup_files_dir / "audio", "backup_copy")):
        p = _inside(root, c.recording_path) if root else None
        if p and p.is_file():
            p.unlink()
            files += 1
            what.append(label)
        if p and label == "backup_copy" and manifest is not None:   # drop its manifest line too (PDPL)
            key = "audio/" + p.relative_to(root.resolve()).as_posix()
            pruned = manifest.pop(key, None) is not None or pruned
    for t in tables:
        if session.execute(text(f'DELETE FROM "{t}" WHERE call_id = :id'), {"id": c.id}).rowcount:
            what.append(t)
    c.recording_status, c.recording_path = "deleted", None
    c.content_deleted_at = c.content_deleted_at or _utcnow()   # follow-up KPIs leave erased calls out
    return what, files, pruned


def delete_backup_copy(backup_files_dir: Path, rel_path: str) -> bool:
    """Worker side of an admin delete (the API cannot write BACKUP_DIR): remove the copy + its manifest line."""
    from .backup import MANIFEST, read_manifest, write_manifest
    root = backup_files_dir / "audio"
    p = _inside(root, rel_path)
    if p is None:
        return False
    gone = False
    if p.is_file():
        p.unlink()
        gone = True
    manifest_path = backup_files_dir / MANIFEST
    manifest = read_manifest(manifest_path)
    if manifest.pop("audio/" + p.relative_to(root.resolve()).as_posix(), None) is not None:
        write_manifest(manifest_path, manifest)
    return gone


def run(session: Session, cfg: RetentionConfig, audio_dir: Path, backup_files_dir: Path | None,
        now: datetime | None = None, force_dry_run: bool = False, teams=None) -> dict:
    """teams (teams.yaml): needed for the KPI snapshots and the "form never filled" open check; None = neither."""
    now = now or _utcnow()
    why = cfg.problem()
    if why:
        return {"enabled": False, "reason": why}
    days = cfg.days
    def ran(*where) -> bool:
        return session.scalar(select(RetentionRun.id).where(RetentionRun.days == days, *where).limit(1)) is not None
    # the first real run needs a recent dry run (its counts were checked); after that every night deletes
    started = ran(RetentionRun.dry_run.is_(False))
    checked = ran(RetentionRun.dry_run.is_(True), RetentionRun.ran_at >= now - timedelta(days=DRY_RUN_VALID_DAYS))
    dry = force_dry_run or not (started or checked)
    keep = {"keep_rating_set": cfg.keep_rating_set, "open_max_days": cfg.open_max_days, "teams": teams}
    calls = select_calls(session, days, now, **keep)
    kept_rating, kept_open = kept_counts(session, days, now, **keep)
    snapshots = 0
    if teams is not None and calls:      # save the old periods' KPI numbers first (also on the dry run: full data)
        from .kpi import save_snapshots
        snapshots = save_snapshots(session, teams, snapshot_days(session, calls), now)
        # saving can take minutes: choose again, so a call that got new open work meanwhile is kept
        calls = select_calls(session, days, now, **keep)
    extra = {"kept_rating": kept_rating, "kept_open": kept_open, "snapshots": snapshots}
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
        log.info("retention DRY RUN (%s days): %s calls, %s recording files, %s audit rows would be deleted; "
                 "kept: %s rating work, %s open work; %s KPI snapshots saved",
                 days, len(calls), files, audit_rows, kept_rating, kept_open, snapshots)
        return {"enabled": True, "dry_run": True, "days": days, "calls": len(calls), "files": files,
                "audit_rows": audit_rows, **extra}

    present = [t for t in CONTENT_TABLES if inspect(session.get_bind()).has_table(t)]
    from .backup import MANIFEST, read_manifest, write_manifest
    manifest_path = backup_files_dir / MANIFEST if backup_files_dir else None
    manifest = read_manifest(manifest_path) if manifest_path else {}
    pruned = False
    for i in range(0, len(calls), BATCH):
        for c in calls[i:i + BATCH]:
            what, n, was_pruned = erase_call_content(session, c, audio_dir, backup_files_dir, manifest, present)
            gold = erase_gold_clips(session, c.id, audio_dir)       # only once keep_rating_set is off
            if gold:
                what.append("gold_clip")
            files += n + gold
            pruned = pruned or was_pruned
            session.add(RetentionLog(run_id=r.id, call_id=c.id, deleted_at=now, what=",".join(what) or "nothing"))
        session.commit()
        if pruned:
            write_manifest(manifest_path, manifest)
            pruned = False
    for model, where in audit_where:
        session.execute(delete(model).where(where))
    r.files = files
    session.commit()
    log.info("retention (%s days): %s calls cleared, %s files deleted, %s audit rows deleted; "
             "kept: %s rating work, %s open work; %s KPI snapshots saved",
             days, len(calls), files, audit_rows, kept_rating, kept_open, snapshots)
    return {"enabled": True, "dry_run": False, "days": days, "calls": len(calls), "files": files,
            "audit_rows": audit_rows, **extra}
