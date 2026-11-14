"""Backups (spec 2026-11-14-hardening, decisions 2-3): `python -m callsight backup` and `backup verify`.

backup: 1) DB first: a REPEATABLE READ transaction exports its snapshot and counts the rows of every table, then
`pg_dump -Fc --snapshot=...` dumps exactly that snapshot (so the saved counts match the dump). 2) Files: recordings
with status ok + archived reports are copied into BACKUP_DIR/files when new or changed (size, mtime +-2 s), each
written as `.tmp` and renamed, with a sha256 manifest. The backup never deletes a file on its own. 3) Rotation keeps
14 daily + 8 weekly dumps and deletes only dumps with our own name pattern.

verify (the restore drill): restores the newest dump into the hard-coded scratch DB `callsight_restore_check`
(dropped before and after: it holds personal data), compares row counts + alembic version with the backup_run row.

Not encrypted, and by default on the same disk as the audio: protects against mistakes, not against disk loss.
`.env` (secrets) is not in the backup: keep a copy of it somewhere safe (docs/scale-out.md).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from .config import RIYADH
from .db.models import BackupRun, Call, ReportArchive

log = logging.getLogger(__name__)

SCRATCH_DB = "callsight_restore_check"          # the ONLY database `verify` ever creates or drops
DAILY_KEEP, WEEKLY_KEEP = 14, 8
DUMP_RE = re.compile(r"^callsight-(\d{8})-(\d{6})\.dump$")
MTIME_SLACK = 2.0                               # seconds (FAT/NAS/Windows mounts round mtimes)
MANIFEST = "manifest.sha256"


class BackupError(Exception):
    """A backup/verify failure. The message is short and holds no paths, hostnames or passwords."""


@dataclass(frozen=True)
class BackupConfig:
    database_url: str
    backup_dir: Path
    audio_dir: Path
    reports_dir: Path

    @property
    def db_dir(self) -> Path:
        return self.backup_dir / "db"

    @property
    def files_dir(self) -> Path:
        return self.backup_dir / "files"


Runner = Callable[..., subprocess.CompletedProcess]


# ---------- connection helpers (password only in the environment, never on a command line) ----------

def _url(database_url: str, database: str | None = None):
    u = make_url(database_url).set(drivername="postgresql")
    return u.set(database=database) if database else u


def libpq_env(database_url: str, database: str | None = None) -> dict[str, str]:
    u = _url(database_url, database)
    env = {**os.environ, "PGHOST": u.host or "localhost", "PGPORT": str(u.port or 5432), "PGUSER": u.username or "",
           "PGDATABASE": u.database or ""}
    if u.password:
        env["PGPASSWORD"] = u.password
    return env


def _connect(database_url: str, database: str | None = None, autocommit: bool = False):
    import psycopg
    return psycopg.connect(_url(database_url, database).render_as_string(hide_password=False),
                           autocommit=autocommit, connect_timeout=10)


def _tables(cur) -> list[str]:
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    return [r[0] for r in cur.fetchall()]


def count_rows(cur, tables: Iterable[str]) -> dict[str, int]:
    from psycopg import sql
    out = {}
    for t in tables:
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("public", t)))
        out[t] = cur.fetchone()[0]
    return out


def _alembic_version(cur, tables: list[str]) -> str | None:
    if "alembic_version" not in tables:
        return None
    cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else None


class Snapshot:
    """REPEATABLE READ transaction kept open while pg_dump uses its exported snapshot."""

    def __init__(self, database_url: str):
        import psycopg
        self.conn = _connect(database_url)
        self.conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        cur = self.conn.cursor()
        cur.execute("SELECT pg_export_snapshot()")
        self.id = cur.fetchone()[0]
        tables = _tables(cur)
        self.counts = count_rows(cur, tables)
        self.alembic_version = _alembic_version(cur, tables)
        cur.execute("SELECT current_setting('server_version_num')::int / 10000, pg_database_size(current_database())")
        self.server_major, self.db_bytes = cur.fetchone()

    def close(self) -> None:
        try:
            self.conn.rollback()
        finally:
            self.conn.close()

    def __enter__(self) -> "Snapshot":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def client_major(runner: Runner = subprocess.run, tool: str = "pg_dump") -> int:
    """Major version of the installed pg_dump/pg_restore ('pg_dump (PostgreSQL) 16.6 (Debian ...)' -> 16)."""
    try:
        out = runner([tool, "--version"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BackupError(f"{tool} not available (install postgresql-client-16)") from exc
    m = re.search(r"\)\s*(\d+)", out) or re.search(r"(\d+)\.\d+", out)
    if not m:
        raise BackupError(f"cannot read the {tool} version")
    return int(m.group(1))


# ---------- rotation ----------

def _dump_time(name: str) -> datetime | None:
    m = DUMP_RE.match(name)
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else None


def _week_start(d: date) -> date:   # Saudi week: Sunday .. Saturday (same as the KPI weeks)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def rotation_victims(names: Iterable[str], daily: int = DAILY_KEEP, weekly: int = WEEKLY_KEEP) -> list[str]:
    """Our dumps to delete: keep the newest dump of each of the newest `daily` days and of the newest `weekly`
    weeks. Names that are not ours (other pattern) are never returned."""
    ours = sorted(((t, n) for n in names if (t := _dump_time(n))), reverse=True)
    keep: set[str] = set()
    days: dict[date, str] = {}
    weeks: dict[date, str] = {}
    for t, n in ours:                      # newest first: first seen = newest of that day/week
        days.setdefault(t.date(), n)
        weeks.setdefault(_week_start(t.date()), n)
    keep |= {days[d] for d in sorted(days, reverse=True)[:daily]}
    keep |= {weeks[w] for w in sorted(weeks, reverse=True)[:weekly]}
    if ours:
        keep.add(ours[0][1])
    return sorted(n for _, n in ours if n not in keep)


def rotate(db_dir: Path) -> list[str]:
    victims = rotation_victims(p.name for p in db_dir.iterdir() if p.is_file())
    for name in victims:
        (db_dir / name).unlink()
    return victims


# ---------- files ----------

def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_hashed(src: Path, dst_tmp: Path) -> str:
    h = hashlib.sha256()
    with src.open("rb") as fi, dst_tmp.open("wb") as fo:
        for chunk in iter(lambda: fi.read(1 << 20), b""):
            h.update(chunk)
            fo.write(chunk)
        fo.flush()
        os.fsync(fo.fileno())
    shutil.copystat(src, dst_tmp)          # keeps the mtime -> next run sees "unchanged"
    return h.hexdigest()


def read_manifest(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, _, key = line.partition("  ")
        if digest and key:
            out[key] = digest
    return out


def write_manifest(path: Path, manifest: dict[str, str]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(f"{manifest[k]}  {k}\n" for k in sorted(manifest)), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class CopyResult:
    copied: int = 0
    bytes: int = 0
    unchanged: int = 0
    missing: int = 0
    refused: int = 0                        # path outside its root (never copied)
    keys: list[str] = field(default_factory=list)


def copy_new(src_root: Path, rels: Iterable[str], dest_root: Path, prefix: str, manifest: dict[str, str],
             res: CopyResult | None = None) -> CopyResult:
    """Copy files (relative paths from DB rows) from src_root to dest_root/prefix when new or changed."""
    res = res or CopyResult()
    src_root, dest_root = src_root.resolve(), dest_root.resolve()
    base = (dest_root / prefix).resolve()
    for rel in rels:
        src = (src_root / rel).resolve()
        if not src.is_relative_to(src_root):
            res.refused += 1
            continue
        if not src.is_file():
            res.missing += 1
            continue
        sub = src.relative_to(src_root).as_posix()
        key = f"{prefix}/{sub}"
        dst = (base / sub).resolve()
        if not dst.is_relative_to(base):
            res.refused += 1
            continue
        st = src.stat()
        if dst.is_file():
            dt = dst.stat()
            if dt.st_size == st.st_size and abs(dt.st_mtime - st.st_mtime) <= MTIME_SLACK:
                if key not in manifest:
                    manifest[key] = file_sha256(dst)
                res.unchanged += 1
                continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        try:
            manifest[key] = _copy_hashed(src, tmp)
            os.replace(tmp, dst)
        finally:
            if tmp.exists():
                tmp.unlink()
        res.copied += 1
        res.bytes += st.st_size
        res.keys.append(key)
    return res


def copy_files(session: Session, cfg: BackupConfig) -> CopyResult:
    cfg.files_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cfg.files_dir / MANIFEST
    manifest = read_manifest(manifest_path)
    recs = session.scalars(select(Call.recording_path).where(Call.recording_status == "ok",
                                                             Call.recording_path.is_not(None)))
    res = copy_new(cfg.audio_dir, recs, cfg.files_dir, "audio", manifest)
    reports = session.scalars(select(ReportArchive.file))
    copy_new(cfg.reports_dir, reports, cfg.files_dir, "reports", manifest, res)
    write_manifest(manifest_path, manifest)
    return res


# ---------- backup ----------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def dump_name(now: datetime) -> str:
    return f"callsight-{now.astimezone(RIYADH):%Y%m%d-%H%M%S}.dump"


def _pg_dump(cfg: BackupConfig, snapshot_id: str, out: Path, runner: Runner) -> None:
    r = runner(["pg_dump", "-Fc", "--no-password", f"--snapshot={snapshot_id}", "-f", str(out)],
               env=libpq_env(cfg.database_url), capture_output=True, text=True)
    if r.returncode != 0:
        log.warning("pg_dump stderr (last line): %s", (r.stderr or "").strip().splitlines()[-1:])
        raise BackupError(f"pg_dump failed (exit {r.returncode})")


def run_backup(session: Session, cfg: BackupConfig, runner: Runner = subprocess.run,
               snapshot: Callable[[str], Snapshot] = Snapshot, now: Callable[[], datetime] = _now,
               free_bytes: Callable[[Path], int] = lambda p: shutil.disk_usage(p).free) -> BackupRun:
    """DB dump first, then files, then rotation. Always leaves a backup_run row (ok / error)."""
    run = BackupRun(started_at=now(), status="running", files_copied=0, files_bytes=0)
    session.add(run)
    session.commit()
    try:
        cfg.db_dir.mkdir(parents=True, exist_ok=True)
        major = client_major(runner)
        with snapshot(cfg.database_url) as snap:
            if major != snap.server_major:
                raise BackupError(f"pg_dump {major} cannot safely dump a PostgreSQL {snap.server_major} server")
            if free_bytes(cfg.db_dir) < snap.db_bytes:
                raise BackupError("not enough free disk space in BACKUP_DIR for the database dump")
            name = dump_name(run.started_at)
            final, tmp = cfg.db_dir / name, cfg.db_dir / (name + ".tmp")
            try:
                _pg_dump(cfg, snap.id, tmp, runner)
                os.replace(tmp, final)
            finally:
                if tmp.exists():
                    tmp.unlink()
            run.dump_file, run.dump_bytes = name, final.stat().st_size
            run.counts, run.alembic_version = snap.counts, snap.alembic_version
        session.commit()
        files = copy_files(session, cfg)
        run.files_copied, run.files_bytes = files.copied, files.bytes
        removed = rotate(cfg.db_dir)
        run.status, run.finished_at = "ok", now()
        session.commit()
        log.info("backup ok: dump %s bytes, %s tables, %s files copied (%s unchanged, %s missing), %s old dumps removed",
                 run.dump_bytes, len(run.counts or {}), files.copied, files.unchanged, files.missing, len(removed))
    except Exception as exc:  # noqa: BLE001  the row must say "error" whatever happened
        session.rollback()
        run = session.get(BackupRun, run.id)
        run.status, run.finished_at = "error", now()
        run.error = str(exc)[:300] if isinstance(exc, BackupError) else f"backup failed ({type(exc).__name__})"
        session.commit()
        log.warning("backup failed: %s", run.error)
    return run


# ---------- verify (restore drill) ----------

@dataclass
class VerifyResult:
    ok: bool
    run_id: int | None = None
    tables: int = 0
    mismatches: dict[str, tuple] = field(default_factory=dict)
    message: str = ""


def _drop_scratch(cur) -> None:
    cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)')


def check_not_live(database_url: str) -> None:
    if (make_url(database_url).database or "") == SCRATCH_DB:
        raise BackupError(f"refusing: the live database is named {SCRATCH_DB}")


def verify(session: Session, cfg: BackupConfig, runner: Runner = subprocess.run,
           connect: Callable = _connect, now: Callable[[], datetime] = _now,
           free_bytes: Callable[[Path], int] = lambda p: shutil.disk_usage(p).free) -> VerifyResult:
    check_not_live(cfg.database_url)
    run = session.scalars(select(BackupRun).where(BackupRun.status == "ok", BackupRun.dump_file.is_not(None))
                          .order_by(BackupRun.id.desc()).limit(1)).first()
    if run is None:
        raise BackupError("no successful backup to verify")
    dump = cfg.db_dir / run.dump_file
    if not dump.is_file():
        raise BackupError("the newest dump file is missing")
    if free_bytes(cfg.backup_dir) < 4 * dump.stat().st_size:     # restored DB is several times the -Fc dump
        raise BackupError("not enough free disk space for the restore drill")
    if client_major(runner, "pg_restore") < 16:
        raise BackupError("pg_restore older than 16")

    admin = connect(cfg.database_url, "postgres", autocommit=True)
    try:
        cur = admin.cursor()
        _drop_scratch(cur)
        cur.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
        try:
            r = runner(["pg_restore", "--no-owner", "--no-acl", "--exit-on-error", "--no-password",
                        "-d", SCRATCH_DB, str(dump)],
                       env=libpq_env(cfg.database_url, SCRATCH_DB), capture_output=True, text=True)
            if r.returncode != 0:
                log.warning("pg_restore stderr (last line): %s", (r.stderr or "").strip().splitlines()[-1:])
                raise BackupError(f"pg_restore failed (exit {r.returncode})")
            scratch = connect(cfg.database_url, SCRATCH_DB, autocommit=True)
            try:
                sc = scratch.cursor()
                tables = _tables(sc)
                got = count_rows(sc, tables)
                version = _alembic_version(sc, tables)
            finally:
                scratch.close()
        finally:
            _drop_scratch(cur)            # personal data: never leave the copy lying around
    finally:
        admin.close()

    want = run.counts or {}
    mismatches = {t: (want.get(t), got.get(t)) for t in sorted(set(want) | set(got)) if want.get(t) != got.get(t)}
    if version != run.alembic_version:
        mismatches["alembic_version"] = (run.alembic_version, version)
    res = VerifyResult(ok=not mismatches and bool(want), run_id=run.id, tables=len(want), mismatches=mismatches)
    res.message = (f"restore drill OK: {len(want)} tables, row counts equal" if res.ok
                   else f"restore drill FAILED: {len(mismatches)} differences")
    run.verified_at, run.verify_ok = now(), res.ok
    session.commit()
    log.info("%s", res.message)
    return res
