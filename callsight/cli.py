"""Command line: python -m callsight <command>

  ingest                 pull new calls now (same as the 2-minute job)
  backfill --days N      load the last N days of team calls
  recordings             download pending recordings now
  reconcile --date D     check one day (YYYY-MM-DD, default yesterday) against the PBX and fill gaps
  sync-agents            refresh agents/queues from the PBX
  forward-sync           branch supervisors: config/forward_people.yaml -> DB, linked to the customer-service
                         system (CS_API_KEY / CS_USER_GUID); prints who is linked
  forward-targets        list the supervisors the customer-service system gives (name, branch, id)
  forward-send           send waiting forwarded problems now (same as the 5-minute job)
  forward-poll           read back the sent tickets now (closed? solution? questions; same as the 5-minute job)
  stats --date D         calls per direction / status / agent for one day
  create-user NAME --role admin|supervisor|agent [--ext 114] [--generate]
                         new web login; asks for the password, or with --generate prints a random one.
                         An agent needs --ext (a team extension in teams.yaml) and sees only their own calls
  set-ext NAME --ext 104 new extension for a user (ends that user's open logins)
  set-password NAME [--generate]   new password (ends that user's open logins)
  disable-user NAME      block a web login
  goldset pick [--n 30 --seconds 60 --seed 1]   choose + cut the ASR gold-set clips (once)
  goldset score [--bench-dir data/asr-bench --out docs/asr-benchmark.md]
  goldset drafts [--bench-dir data/asr-bench]   pre-fill model drafts (rotating source) for the typist to correct
                         WER/CER per model from the typed texts -> benchmark report
  report --period week|month [--start YYYY-MM-DD] [--team T] [--agent EXT] [--pdf out.pdf]
                         print the KPIs of the week (Sun-Sat) / month containing --start
                         (default: the last closed one); --pdf also writes the PDF report
  report-archive [--period week|month]   (re)build the archived PDF of the last closed period(s) now
  backup                 DB dump + copy of new recordings/reports into BACKUP_DIR (also the step before
                         every migration). In Docker: docker compose run --rm worker python -m callsight backup
  backup verify          restore drill: newest dump -> scratch DB callsight_restore_check, compare row counts
  retention [--dry-run]  PDPL clean-up now (only when RETENTION_ENABLED=true and days >= 30 in retention.yaml)
  pbx-cert --fetch       show the SHA-256 fingerprint of the PBX's certificate (office LAN; compare it with the
                         PBX web page); --accept SHA256 saves it to YEASTAR_CA_FILE only when it matches
  transcribe --call ID [--force]   queue one call for the asr worker (--force: redo done/error/skipped)
  transcribe --days N --limit M    backfill: newest M answered calls of the last N days (max 500)
  transcribe status                queue length, done/error counts, latency hang-up -> transcript
  transcribe --retry-errors --since YYYY-MM-DD [--limit 50]   re-queue failed calls since that day (max 500)
  transcripts reindex              rebuild the normalised search text of all done transcripts
  analyze --call ID [--force]      queue one call for the nlp worker (local LLM summary + first estimate)
  analyze --days N --limit M [--rescore]   backfill: newest M transcribed calls of the last N days without an
                                   analysis (--rescore: without one by the current LLM_MODEL + prompt/topics)
  analyze status                   analysis counts for the current model + prompt version, nlp queue length
  flags --recompute [--call ID]    re-match config/rules.yaml on the transcripts (after editing the word lists)
  blindset create [--n 100 --min-pool 100]   freeze the blind test set once (it never changes afterwards)
  blindset status                  blind set size and rating progress
  pbx-unblock            allow PBX logins again after a refused login / token limit set pbx:auth_blocked
                         (fix the cause first: Client ID/Secret, PBX API IP allow-list, other programs' logins)
  gpu-work [--queue]     AWS: how many calls still wait for a transcript or an analysis (one number; 0 = the
                         GPU server may power off). --queue also queues them now (no wait for the 5-min jobs)
  live                   live call board service (one PBX WebSocket). Does nothing unless LIVE_ENABLED=true;
                         in Docker: docker compose --profile live up -d live   (office LAN only)
"""
from __future__ import annotations

import argparse
import getpass
import logging
import sys
from datetime import date, datetime, timedelta

from . import app as wiring
from .config import RIYADH
from .ingest import service
from .pbx.base import PbxError


def _day(text: str | None) -> date:
    return date.fromisoformat(text) if text else (datetime.now(RIYADH) - timedelta(days=1)).date()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m callsight", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest")
    b = sub.add_parser("backfill"); b.add_argument("--days", type=int, default=7)
    r = sub.add_parser("recordings"); r.add_argument("--limit", type=int, default=500)
    c = sub.add_parser("reconcile"); c.add_argument("--date")
    sub.add_parser("sync-agents")
    st = sub.add_parser("stats"); st.add_argument("--date")
    cu = sub.add_parser("create-user"); cu.add_argument("username")
    cu.add_argument("--role", choices=("admin", "supervisor", "agent"), required=True); cu.add_argument("--ext")
    cu.add_argument("--generate", action="store_true", help="random password, printed once")
    sp = sub.add_parser("set-password"); sp.add_argument("username")
    sp.add_argument("--generate", action="store_true", help="random password, printed once")
    du = sub.add_parser("disable-user"); du.add_argument("username")
    se = sub.add_parser("set-ext"); se.add_argument("username"); se.add_argument("--ext", required=True)
    g = sub.add_parser("goldset"); g.add_argument("action", choices=("pick", "score", "drafts"))
    g.add_argument("--n", type=int, default=30); g.add_argument("--seconds", type=float, default=60.0)
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--bench-dir", default="data/asr-bench"); g.add_argument("--out", default="docs/asr-benchmark.md")
    rp = sub.add_parser("report"); rp.add_argument("--period", choices=("week", "month"), default="week")
    rp.add_argument("--start", type=date.fromisoformat); rp.add_argument("--team"); rp.add_argument("--agent")
    rp.add_argument("--pdf", help="write the PDF report to this file")
    ra = sub.add_parser("report-archive"); ra.add_argument("--period", choices=("week", "month"))
    bk = sub.add_parser("backup"); bk.add_argument("action", nargs="?", choices=("run", "verify"), default="run")
    bk.add_argument("--no-record", action="store_true", help="write nothing to the database (no backup_run row)")
    rt = sub.add_parser("retention"); rt.add_argument("--dry-run", action="store_true")
    pc = sub.add_parser("pbx-cert"); g2 = pc.add_mutually_exclusive_group(required=True)
    g2.add_argument("--fetch", action="store_true"); g2.add_argument("--accept", metavar="SHA256")
    pc.add_argument("--out", help="where to save the PEM (default: YEASTAR_CA_FILE or config/pbx-ca.pem)")
    tr = sub.add_parser("transcribe"); tr.add_argument("action", nargs="?", choices=("status",))
    tr.add_argument("--call", type=int); tr.add_argument("--force", action="store_true")
    tr.add_argument("--days", type=int); tr.add_argument("--limit", type=int)
    tr.add_argument("--retry-errors", action="store_true"); tr.add_argument("--since", type=date.fromisoformat)
    ts = sub.add_parser("transcripts"); ts.add_argument("action", choices=("reindex",))
    an = sub.add_parser("analyze"); an.add_argument("action", nargs="?", choices=("status",))
    an.add_argument("--call", type=int); an.add_argument("--force", action="store_true")
    an.add_argument("--days", type=int); an.add_argument("--limit", type=int)
    an.add_argument("--rescore", action="store_true")
    fl = sub.add_parser("flags"); fl.add_argument("--recompute", action="store_true", required=True)
    fl.add_argument("--call", type=int)
    bs = sub.add_parser("blindset"); bs.add_argument("action", choices=("create", "status"))
    bs.add_argument("--n", type=int, default=100); bs.add_argument("--min-pool", type=int, default=100)
    sub.add_parser("pbx-unblock")
    gw = sub.add_parser("gpu-work"); gw.add_argument("--queue", action="store_true")
    sub.add_parser("live")
    for name in ("forward-sync", "forward-targets", "forward-send", "forward-poll"):
        sub.add_parser(name)
    a = p.parse_args(argv)
    if a.cmd in ("forward-sync", "forward-targets", "forward-send", "forward-poll"):
        return _forward(a.cmd)
    if a.cmd == "pbx-unblock":
        return _pbx_unblock()
    if a.cmd == "gpu-work":
        return _gpu_work(a)
    if a.cmd == "live":
        from .live.service import main as live_main
        return live_main()
    if a.cmd == "backup":
        return _backup(a)
    if a.cmd == "retention":
        return _retention(a)
    if a.cmd == "pbx-cert":
        return _pbx_cert(a)
    if a.cmd == "report":
        return _report(a)
    if a.cmd == "report-archive":
        return _report_archive(a)
    if a.cmd == "transcribe":
        return _transcribe(a, p)
    if a.cmd == "goldset":
        return _goldset(a)
    if a.cmd == "transcripts":
        return _transcripts_reindex()
    if a.cmd == "analyze":
        return _analyze(a, p)
    if a.cmd == "flags":
        return _flags(a)
    if a.cmd == "blindset":
        return _blindset(a)
    if a.cmd in ("create-user", "set-password", "disable-user", "set-ext"):
        return _users(a)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # URLs contain the access token

    pbx = wiring.pbx()
    try:
        with wiring.session_factory()() as s:
            if a.cmd == "ingest":
                print(service.run_ingest(s, pbx, wiring.teams()))
            elif a.cmd == "backfill":
                if not 1 <= a.days <= 90:
                    p.error("--days must be 1..90")
                print(service.run_backfill(s, pbx, wiring.teams(), a.days))
            elif a.cmd == "recordings":
                ok, failed = service.fetch_recordings(s, pbx, wiring.settings().audio_dir, limit=a.limit)
                print(f"recordings: {ok} downloaded, {failed} failed")
            elif a.cmd == "reconcile":
                run = service.reconcile_day(s, pbx, wiring.teams(), wiring.settings().audio_dir, _day(a.date))
                print(f"{run.day}: PBX team calls {run.pbx_team_calls}, in DB {run.db_team_calls}; "
                      f"legs {run.pbx_team_legs}, missing before {run.missing_legs_before} -> after {run.missing_legs_after}; "
                      f"recordings {run.recordings_expected}, missing before {run.missing_recordings_before} "
                      f"-> after {run.missing_recordings_after}; {'OK' if run.ok else 'NOT OK'}")
            elif a.cmd == "sync-agents":
                n_ext, n_q = service.sync_agents(s, pbx, wiring.teams())
                print(f"synced {n_ext} extensions, {n_q} queues")
            elif a.cmd == "stats":
                day = _day(a.date)
                rows = service.day_stats(s, day)
                print(f"{day} (Riyadh time): {sum(r[3] for r in rows)} team calls")
                for direction, status, ext, n in rows:
                    print(f"  {direction:9} {status:16} ext {ext or '-':6} {n}")
    except PbxError as exc:
        print("PBX ERROR:", exc, file=sys.stderr)
        return 1
    finally:
        pbx.close()
    return 0


def _ask_password(generate: bool) -> tuple[str, bool]:
    """Random (printed once) with --generate, otherwise typed twice."""
    from .api.auth import generate_password
    if generate:
        return generate_password(), True
    while True:
        pw = getpass.getpass("New password (min 10 characters): ")
        if pw == getpass.getpass("Repeat password: "):
            return pw, False
        print("Passwords differ, try again.")


def _users(a: argparse.Namespace) -> int:
    from .api import auth
    try:
        with wiring.session_factory()() as s:
            if a.cmd == "disable-user":
                auth.disable_user(s, a.username)
                print(f"user {a.username} disabled")
                return 0
            if a.cmd == "set-ext":
                auth.set_ext(s, a.username, a.ext, wiring.teams().ext_team)
                print(f"user {a.username} now has extension {a.ext} (open logins ended)")
                return 0
            pw, generated = _ask_password(a.generate)
            if a.cmd == "create-user":
                auth.create_user(s, a.username, pw, a.role, a.ext, wiring.teams().ext_team)
                print(f"user {a.username.lower()} created ({a.role})")
            else:
                auth.set_password(s, a.username, pw)
                print(f"password changed for {a.username}")
            if generated:
                print(f"password: {pw}   <- write it down now, it is not shown again")
    except ValueError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    return 0


def _transcripts_reindex() -> int:
    """Rebuild the normalised search text of every done transcript (after search_normalize() changes)."""
    from .transcripts import reindex
    with wiring.session_factory()() as s:
        n = reindex(s)
    print(f"{n} transcript(s) reindexed for search")
    return 0


def _analyze(a: argparse.Namespace, p: argparse.ArgumentParser) -> int:
    """DB + Redis only. Run where Redis is reachable, e.g. inside the worker container."""
    from . import jobs
    from .analysis import llm
    from .analysis import queueing as nlpq
    from .analysis.settings import ConfigError, LlmConfig, load_topics
    modes = sum((a.action == "status", a.call is not None, a.days is not None or a.limit is not None))
    if modes != 1:
        p.error("analyze: give exactly one of: status | --call ID | --days N --limit M [--rescore]")
    if a.call is None and a.action is None and (a.days is None or a.limit is None):
        p.error("analyze: backfill needs both --days and --limit")
    try:
        cfg = LlmConfig.from_env()
        pv = llm.prompt_version(load_topics(cfg.topics_file))
    except (ConfigError, OSError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    now = datetime.now(RIYADH)
    with wiring.session_factory()() as s:
        if a.action == "status":
            counts = nlpq.status_counts(s, cfg.model, pv)
            auto = "on" if cfg.enabled else "OFF (set NLP_ENABLED=true)"
            print(f"model: {cfg.model}   prompt: {pv}   automatic: {auto}")
            digest = llm.Ollama(cfg).model_digest()     # a tag can be re-pointed: compare this with the report
            print(f"model digest: {digest[:12] if digest else 'unknown (Ollama not reachable from here, or model not pulled)'}")
            print(f"nlp queue length: {jobs.asr_queue_length(jobs.NLP_QUEUE)}")
            print("analyses: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none yet"))
            return 0
        try:
            if a.call is not None:
                status, send = nlpq.queue_call(s, a.call, cfg.model, pv, a.force, now)
                ids = [a.call] if send else []
                print(f"call {a.call}: {status}" + ("" if send else " (nothing sent; --force to redo)"))
            else:
                ids = nlpq.queue_recent(s, cfg.model, pv, a.days, a.limit, now, rescore=a.rescore)
        except ValueError as exc:
            print("ERROR:", exc, file=sys.stderr)
            return 1
    try:
        for cid in ids:
            jobs.send_analyze(cid, cfg.model, pv)
    except Exception as exc:                              # noqa: BLE001
        print(f"ERROR: queue not reachable ({type(exc).__name__}); rows stay queued, the reaper re-sends them",
              file=sys.stderr)
        return 1
    print(f"{len(ids)} call(s) sent to the nlp queue for {cfg.model} ({pv})")
    return 0


def _gpu_work(a: argparse.Namespace) -> int:
    """DB (+ Redis with --queue). Prints ONE number on stdout; details go to stderr. Any error = exit code 1
    and no number, so the GPU server never powers off because of a failed check."""
    from . import gpuwork
    from .analysis.settings import LlmConfig
    from .asr.queueing import AsrConfig
    try:
        cfg, nlp = AsrConfig.from_env(), LlmConfig.from_env().enabled
        with wiring.session_factory()() as s:
            if a.queue:
                from . import jobs
                gpuwork.queue_now(s, cfg, nlp, jobs.queue_transcripts, jobs.queue_analyses)
            work = gpuwork.pending(s, cfg, nlp)
    except Exception as exc:                              # noqa: BLE001  messages may hold the DB address
        print(f"ERROR: work left unknown ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"transcripts {work.transcripts}, analyses {work.analyses}"
          + ("" if nlp else " (NLP_ENABLED is off: analyses are not counted)"), file=sys.stderr)
    print(work.total)
    return 0


def _flags(a: argparse.Namespace) -> int:
    import os
    from .analysis import flags
    from .analysis.settings import ConfigError, LlmConfig, load_rules
    try:
        rules = load_rules(LlmConfig.from_env().rules_file)
    except (ConfigError, OSError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    with wiring.session_factory()() as s:
        n = flags.recompute(s, rules, os.getenv("ASR_MODEL", "whisper-large-v3").strip(), call_id=a.call)
    print(f"flags recomputed for {n} call(s) (rules {rules.version})")
    return 0


def _blindset(a: argparse.Namespace) -> int:
    from .analysis import blindset
    with wiring.session_factory()() as s:
        if a.action == "create":
            try:
                n = blindset.freeze(s, n=a.n, min_pool=a.min_pool)
            except ValueError as exc:
                print("ERROR:", exc, file=sys.stderr)
                return 1
            print(f"blind set frozen: {n} calls (never changes; LLM estimates are now shown for the other calls)")
            return 0
        pr = blindset.progress(s)
        state = f"frozen, {blindset.size(s)} calls" if pr["blind_frozen"] else "NOT frozen"
        print(f"blind set: {state}; pool now {len(blindset.pool(s))} transcribed calls")
        print(f"labels: {pr['labels']} / {pr['labels_target']} calls rated; blind test set: "
              f"{pr['blind_rated']} / {pr['blind_target']} rated without pre-fill")
    return 0


def _goldset(a: argparse.Namespace) -> int:
    if a.action == "score":
        return _goldset_score(a)
    if a.action == "drafts":
        return _goldset_drafts(a)
    from collections import Counter
    from .asr.goldset import pick
    from .db.models import Call
    team = wiring.teams()
    try:
        with wiring.session_factory()() as s:
            clips = pick(s, wiring.settings().audio_dir, sorted(team.ext_team), n=a.n, seconds=a.seconds, seed=a.seed)
            calls = [s.get(Call, c.call_id) for c in clips]
    except (ValueError, OSError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    print(f"{len(clips)} clips cut into {wiring.settings().audio_dir / 'goldset'}")
    print("per agent:", dict(Counter(c.agent_ext for c in calls)), "| per direction:", dict(Counter(c.direction for c in calls)))
    return 0


def _goldset_drafts(a: argparse.Namespace) -> int:
    import json
    from collections import Counter
    from pathlib import Path
    from .asr.goldset import make_drafts
    drafts = make_drafts(Path(a.bench_dir))
    if not drafts:
        print("ERROR: no benchmark results found in", a.bench_dir, file=sys.stderr)
        return 1
    out = wiring.settings().audio_dir / "goldset" / "drafts.json"
    out.write_text(json.dumps(drafts, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(drafts)} drafts written to {out}; per model:", dict(Counter(d["source"] for d in drafts.values())))
    return 0


def _goldset_score(a: argparse.Namespace) -> int:
    from pathlib import Path, PurePosixPath
    from sqlalchemy import select
    from .asr.report import build_report
    from .db.models import GoldClip
    with wiring.session_factory()() as s:
        clips = s.scalars(select(GoldClip)).all()
    refs = {PurePosixPath(c.file).name: c.reference_text for c in clips if (c.reference_text or "").strip()}
    bench_dir = Path(a.bench_dir)
    if not clips or not bench_dir.is_dir():
        print("ERROR: no gold set or no benchmark results folder", file=sys.stderr)
        return 1
    text, examples, results = build_report(refs, bench_dir, len(clips))
    (bench_dir / "asr-benchmark-examples.md").write_text(examples, encoding="utf-8")   # git-ignored: customer speech
    Path(a.out).write_text(text + "\n", encoding="utf-8")
    for r in results:
        print(f"{r.model}: {r.clips} clips, WER {r.score.wer:.1%}, CER {r.score.cer:.1%}")
    print(f"{len(refs)}/{len(clips)} texts typed; report written to {a.out}")
    return 0


def _report(a: argparse.Namespace) -> int:
    from pathlib import Path
    from . import kpi
    period = kpi.period_for(a.period, a.start) if a.start else kpi.last_closed(a.period)
    teams = wiring.teams()
    try:
        with wiring.session_factory()() as s:
            data = kpi.report(s, period, teams, team=a.team, agent=a.agent)
            print(kpi.format_text(data))
            if a.pdf:
                from .reports.pdf import make_pdf
                Path(a.pdf).write_bytes(make_pdf(s, period, teams, team=a.team, agent=a.agent))
                print(f"PDF written to {a.pdf}")
    except ValueError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    return 0


def _report_archive(a: argparse.Namespace) -> int:
    from .reports import archive
    kinds = [a.period] if a.period else ["week", "month"]
    with wiring.session_factory()() as s:
        for kind in kinds:
            row = archive.generate(s, kind, wiring.teams(), wiring.reports_dir())
            print(f"{kind} {row.period_start}: {row.file} ({row.size_bytes} bytes)")


def _transcribe(a: argparse.Namespace, p: argparse.ArgumentParser) -> int:
    """DB + Redis only (never the PBX). Run where Redis is reachable, e.g. inside the worker container."""
    from . import jobs
    from .asr import queueing
    modes = sum((a.action == "status", a.call is not None, a.retry_errors,
                 not a.retry_errors and (a.days is not None or a.limit is not None)))
    if modes != 1:
        p.error("transcribe: give exactly one of: status | --call ID | --days N --limit M | --retry-errors --since D")
    if a.retry_errors and (a.since is None or a.days is not None):
        p.error("transcribe: --retry-errors needs --since YYYY-MM-DD (and optional --limit)")
    if not a.retry_errors and (a.days is not None and a.limit is None or a.limit is not None and a.days is None):
        p.error("transcribe: backfill needs both --days and --limit")
    cfg = queueing.AsrConfig.from_env()
    now = datetime.now(RIYADH)
    with wiring.session_factory()() as s:
        if a.action == "status":
            print(queueing.format_status(queueing.status(s, cfg, jobs.asr_queue_length(), now)))
            return 0
        try:
            if a.call is not None:
                status, send = queueing.queue_call(s, cfg, a.call, a.force, now)
                ids = [a.call] if send else []
                print(f"call {a.call}: {status}" + ("" if send else " (nothing sent; --force to redo)"))
            elif a.retry_errors:
                ids = queueing.retry_errors(s, cfg, a.since, a.limit or 50, now)
            else:
                ids = queueing.queue_recent(s, cfg, a.days, a.limit, now)
        except ValueError as exc:
            print("ERROR:", exc, file=sys.stderr)
            return 1
    try:
        for cid in ids:
            jobs.send_transcribe(cid, cfg.model)
    except Exception as exc:                              # noqa: BLE001
        print(f"ERROR: queue not reachable ({type(exc).__name__}); rows stay queued and the reaper re-sends "
              "them within ~40 min once Redis is back", file=sys.stderr)
        return 1
    print(f"{len(ids)} call(s) sent to the asr queue for {cfg.model}")
    return 0


def _backup(a: argparse.Namespace) -> int:
    from . import backup
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = wiring.backup_config()
    with wiring.session_factory()() as s:
        if a.action == "verify":
            try:
                res = backup.verify(s, cfg, record=False if a.no_record else None)
            except backup.BackupError as exc:
                print("ERROR:", exc, file=sys.stderr)
                return 1
            print(res.message)
            for table, (want, got) in res.mismatches.items():
                print(f"  {table}: backup {want} / restored {got}")
            return 0 if res.ok else 1
        with _maint_lock() as got:
            if not got:
                print("ERROR: another backup/retention run holds the maintenance lock", file=sys.stderr)
                return 1
            run = backup.run_backup(s, cfg, record=False if a.no_record else None)
    if run.status != "ok":
        print(f"ERROR: backup failed: {run.error}", file=sys.stderr)
        return 1
    print(f"backup ok: {run.dump_file} ({run.dump_bytes} bytes, {len(run.counts or {})} tables); "
          f"{run.files_copied} new files copied ({run.files_bytes} bytes)")
    print("note: not encrypted, and on the same disk as the audio unless BACKUP_HOST_DIR points to another disk/NAS")
    return 0


def _maint_lock():
    """The Redis maintenance lock when Redis is reachable (Docker); without Redis (PC tests) run unlocked."""
    import contextlib
    from .locks import maint_lock
    try:
        import redis
        r = redis.Redis.from_url(wiring.settings().redis_url, socket_timeout=2, socket_connect_timeout=2)
        r.ping()
    except Exception:  # noqa: BLE001
        print("note: Redis not reachable, running without the maintenance lock", file=sys.stderr)
        return contextlib.nullcontext(True)
    return maint_lock(r)


def _retention(a: argparse.Namespace) -> int:
    from . import retention
    cfg = wiring.retention_config()
    with wiring.session_factory()() as s, _maint_lock() as got:
        if not got:                              # same lock as the nightly backup/retention (asr waits too)
            print("ERROR: another backup/retention run holds the maintenance lock", file=sys.stderr)
            return 1
        r = retention.run(s, cfg, wiring.settings().audio_dir, wiring.backup_config().files_dir,
                          force_dry_run=a.dry_run)
    if not r.get("enabled"):
        print(r["reason"])
        return 0
    print(("DRY RUN, nothing deleted: " if r["dry_run"] else "deleted: ") +
          f"{r['calls']} calls older than {r['days']} days, {r['files']} recording files, {r['audit_rows']} audit rows")
    return 0


def _pbx_cert(a: argparse.Namespace) -> int:
    from pathlib import Path
    from .config import ROOT
    from .pbx import tls
    s = wiring.settings()
    try:
        if a.fetch:
            host, port = tls.host_port(s.yeastar_base_url)
            print(f"SHA-256 fingerprint of the certificate the PBX presents now:\n  {tls.fingerprint(tls.fetch_cert(host, port))}")
            print("Compare it with the fingerprint shown in the PBX web interface (or the certificate you download "
                  "there). Only if they are equal: python -m callsight pbx-cert --accept <fingerprint>")
            return 0
        out = Path(a.out or s.yeastar_ca_file or ROOT / "config" / "pbx-ca.pem")
        fp = tls.accept(s.yeastar_base_url, a.accept, out)
        print(f"saved {out} ({fp}). Now set YEASTAR_CA_FILE={out} in .env and restart.")
        return 0
    except (ValueError, OSError, tls.PbxError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1


def _pbx_unblock(redis=None) -> int:
    from .pbx.authblock import unblock
    try:
        info = unblock(redis if redis is not None else wiring.redis_client())
    except Exception as exc:  # noqa: BLE001 - redis connection errors
        print(f"ERROR: cannot reach Redis ({type(exc).__name__}). In Docker: "
              "docker compose run --rm worker python -m callsight pbx-unblock", file=sys.stderr)
        return 1
    if info is None:
        print("PBX logins were not blocked.")
    else:
        print(f"PBX logins allowed again (was blocked by {info.get('by', '?')}: {info.get('reason', '?')}).")
    return 0


def _forward(cmd: str) -> int:
    """Part 4 (spec 2026-11-16-priority-no-followup): the link to the customer-service system."""
    from . import forward
    from .cs_api import CsClient, CsConfig, CsError, CsUnavailable, supervisors
    cfg = CsConfig.from_env()
    if not cfg.enabled and cmd != "forward-sync":
        print("CS_API_KEY / CS_USER_GUID are empty in .env: the link is off")
        return 1
    try:
        with wiring.session_factory()() as s:
            if cmd == "forward-sync":
                if not cfg.enabled:
                    print("link off: only the yaml is loaded (no ids)")
                    lines = forward.sync_people(s, None)
                else:
                    with CsClient(cfg) as client:
                        lines = forward.sync_people(s, client)
                for line in lines:
                    print(line)
                return 0 if not any(x.startswith("NOT LINKED") for x in lines) else 1
            with CsClient(cfg) as client:
                if cmd == "forward-targets":
                    for x in supervisors(client.forward_targets()):
                        print(f"{x['group']:5} {x['branch']} | {x['name']} | {x['guid']}")
                    return 0
                if cmd == "forward-poll":
                    print(forward.poll_tickets(s, client))
                    return 0
                print(forward.send_pending(s, client))
                return 0
    except (CsError, CsUnavailable) as exc:
        print(f"customer-service system: {exc}")
        return 1
