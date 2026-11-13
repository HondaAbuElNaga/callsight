"""Command line: python -m callsight <command>

  ingest                 pull new calls now (same as the 2-minute job)
  backfill --days N      load the last N days of team calls
  recordings             download pending recordings now
  reconcile --date D     check one day (YYYY-MM-DD, default yesterday) against the PBX and fill gaps
  sync-agents            refresh agents/queues from the PBX
  stats --date D         calls per direction / status / agent for one day
  create-user NAME --role admin|supervisor [--ext 114]   new web login (asks for the password,
                         or prints a random one when not run in a terminal)
  set-password NAME      new password (ends that user's open logins)
  disable-user NAME      block a web login
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
    cu.add_argument("--role", choices=("admin", "supervisor"), required=True); cu.add_argument("--ext")
    sp = sub.add_parser("set-password"); sp.add_argument("username")
    du = sub.add_parser("disable-user"); du.add_argument("username")
    a = p.parse_args(argv)
    if a.cmd in ("create-user", "set-password", "disable-user"):
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


def _ask_password() -> tuple[str, bool]:
    """Typed twice in a terminal; otherwise a random one that is printed once."""
    from .api.auth import generate_password
    if not sys.stdin.isatty():
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
            pw, generated = _ask_password()
            if a.cmd == "create-user":
                auth.create_user(s, a.username, pw, a.role, a.ext)
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
