"""Retention keep rules + KPI snapshots (user decisions 2026-11-22): 90 days; rating work and the 30 check calls kept
until the model is trained; open work kept until closed (at most 180 days); KPI numbers saved before deleting."""
from datetime import date, timedelta

from sqlalchemy import func, select, text

from callsight import kpi, retention
from callsight.db.models import (AppUser, BlindSet, Call, CallFollowup, CallFollowupNote, CallShare, GoldClip,
                                 KpiSnapshot, RatingPick, SatisfactionLabel)
from callsight.retention import RetentionConfig
from test_api import engine  # noqa: F401  (fixture)
from test_kpi import TEAM
from test_retention import NOW, ON, env  # noqa: F401  (fixture)


def _old(s, uid, days=100, path=None):
    t = NOW - timedelta(days=days)
    c = Call(uid=uid, direction="inbound", entry="queue", start_time=t, end_time=t, status="answered",
             recording_status="ok", recording_path=path)
    s.add(c)
    s.flush()
    return c


def _user(s):
    return s.scalar(select(AppUser.id).limit(1))


def _form(s, c, solved, no_followup=False):
    u = _user(s)
    s.add(CallFollowup(call_id=c.id, owner_ext="104", no_followup=no_followup, solved=solved, filled_by=u,
                       first_filled_at=c.end_time, updated_by=u, updated_at=c.end_time))


def _uids(calls):
    return sorted(c.uid for c in calls)


def test_shipped_config_is_90_days_keeping_rating_work_and_open_work(tmp_path):
    from callsight.config import ROOT
    cfg = RetentionConfig.load(ROOT / "config" / "retention.yaml", env={"RETENTION_ENABLED": "true"})
    assert (cfg.problem(), cfg.days, cfg.keep_rating_set, cfg.open_max_days) == (None, 90, True, 180)
    assert RetentionConfig.load(ROOT / "config" / "retention.yaml", env={}).problem()     # still off without .env
    f = tmp_path / "r.yaml"
    f.write_text("days: 200\n", encoding="utf-8")                  # keys missing: keep (safe side), open >= days
    cfg = RetentionConfig.load(f, env={"RETENTION_ENABLED": "true"})
    assert (cfg.keep_rating_set, cfg.open_max_days) == (True, 200)
    f.write_text("days: 90\nkeep_rating_set: false\nopen_max_days: 120\n", encoding="utf-8")
    cfg = RetentionConfig.load(f, env={"RETENTION_ENABLED": "true"})
    assert (cfg.keep_rating_set, cfg.open_max_days) == (False, 120)


def test_rating_work_and_check_calls_are_kept_until_switched_off(env):
    Session, *_ = env
    with Session() as s:
        picked, blind, rated, gold, plain = (_old(s, u) for u in ("picked", "blind", "rated", "gold", "plain"))
        s.add_all([RatingPick(call_id=picked.id, rank=1, day=date(2026, 8, 6), salt="x"),
                   BlindSet(call_id=blind.id, rank=1, sampler_version="v", salt="x"),
                   SatisfactionLabel(call_id=rated.id, user_id=_user(s), score=7, blind=False),
                   GoldClip(call_id=gold.id, start_seconds=0, seconds=60, file="g.wav")])
        s.commit()
        gone = retention.select_calls(s, 90, NOW)
        assert "plain" in _uids(gone) and not {"picked", "blind", "rated", "gold"} & set(_uids(gone))
        assert retention.kept_counts(s, 90, NOW) == (4, 0)
        off = retention.select_calls(s, 90, NOW, keep_rating_set=False)
        assert {"picked", "blind", "rated", "gold", "plain"} <= set(_uids(off))


def test_open_work_is_kept_until_closed_at_most_180_days(env):
    Session, *_ = env
    with Session() as s:
        open_, solved, nofu, capped, later, share_open, share_closed = (
            _old(s, "open"), _old(s, "solved"), _old(s, "nofu"), _old(s, "capped", days=200), _old(s, "later"),
            _old(s, "share_open"), _old(s, "share_closed"))
        _form(s, open_, False)
        _form(s, solved, True)
        _form(s, nofu, None, no_followup=True)
        _form(s, capped, False)                                   # open, but older than 180 days: deleted anyway
        _form(s, later, False)                                    # not solved in the form, solved in a later note
        s.flush()
        s.add(CallFollowupNote(call_id=later.id, round=2, what_happened="x", solved=True, created_by=_user(s), created_at=NOW))
        u = _user(s)
        s.add_all([CallShare(call_id=share_open.id, from_user_id=u, to_user_id=u, note="n"),
                   CallShare(call_id=share_closed.id, from_user_id=u, to_user_id=u, note="n",
                             agent_closed_at=NOW)])
        s.commit()
        gone = set(_uids(retention.select_calls(s, 90, NOW)))
        assert {"solved", "nofu", "capped", "later", "share_closed"} <= gone
        assert not {"open", "share_open"} & gone
        assert retention.kept_counts(s, 90, NOW)[1] == 2
        assert "open" in _uids(retention.select_calls(s, 90, NOW, open_max_days=90))


def test_kept_counts_and_snapshots_in_the_run_result(env, engine):  # noqa: F811
    Session, audio, files, _ = env
    with engine.begin() as c:
        c.execute(text("TRUNCATE kpi_snapshot RESTART IDENTITY"))
    with Session() as s:
        s.add(RatingPick(call_id=_old(s, "picked").id, rank=1, day=date(2026, 8, 6), salt="x"))
        s.commit()
        r = retention.run(s, ON, audio, files, NOW, teams=TEAM)           # dry run: numbers saved from full data
        assert r["dry_run"] and r["kept_rating"] == 1 and r["kept_open"] == 0
        # the old calls are on 2026-08-06: week of Sun 2 Aug + month Aug, each for all / 2 teams / 3 agents
        assert r["snapshots"] == 12 == s.scalar(select(func.count()).select_from(KpiSnapshot))
        assert retention.run(s, ON, audio, files, NOW, teams=TEAM)["snapshots"] == 0    # never saved twice
        assert s.scalar(select(Call.recording_status).where(Call.uid == "picked")) == "ok"


def test_report_reads_saved_numbers_for_erased_content(env, engine):  # noqa: F811
    Session, *_ = env
    with engine.begin() as c:
        c.execute(text("TRUNCATE kpi_snapshot RESTART IDENTITY"))
    aug, jul = kpi.period_for("month", date(2026, 8, 6)), kpi.period_for("month", date(2026, 7, 6))
    with Session() as s:
        s.add_all([KpiSnapshot(kind="month", start=aug.start, scope="all", saved_at=NOW,
                               data={"followup": {"saved": 1}, "shares": {"saved": 2}, "agent_score": {"saved": 3},
                                     "summary": {"talk": {"calls": 9}}, "agents": [{"ext": "104", "talk": {"calls": 7}}]}),
                   KpiSnapshot(kind="month", start=jul.start, scope="all", saved_at=NOW,
                               data={"summary": {"talk": {"calls": 3}}})])
        s.commit()
        d = kpi.report(s, aug, TEAM, now=NOW)
        assert d["followup"] == {"saved": 1} and d["shares"] == {"saved": 2} and d["saved_numbers_at"]
        assert "agent_score" not in d                                      # PDF path: no employee KPI %
        assert kpi.report(s, aug, TEAM, now=NOW, with_agent_score=True)["agent_score"] == {"saved": 3}
        assert "agent_score" in kpi._content_figures(kpi.report(s, aug, TEAM, now=NOW, with_agent_score=True))
        assert d["summary"]["talk"] == {"calls": 9} and d["previous"]["talk"] == {"calls": 3}
        assert {a["ext"]: a["talk"] for a in d["agents"]}["104"] == {"calls": 7}
        assert "saved_numbers_at" not in kpi.report(s, aug, TEAM, agent="104", now=NOW)   # other scope: live
        assert "saved_numbers_at" not in kpi.report(s, kpi.date_range(aug.start, aug.end - timedelta(days=1)),
                                                    TEAM, now=NOW)                        # custom range: live


def test_open_forward_and_never_filled_form_are_kept(env):
    from callsight import followups as fu
    from callsight.db.models import ForwardHop, ForwardPerson
    Session, *_ = env
    with Session() as s:
        hop, unfilled = _old(s, "hop"), _old(s, "unfilled")
        unfilled.agent_ext, unfilled.talk_seconds = "104", 60
        p = ForwardPerson(branch="b", name="n")
        s.add(p)
        s.flush()
        s.add(ForwardHop(call_id=hop.id, level=1, person_id=p.id, result="open"))
        fu.set_setting(s, fu.SETTING_START, date(2026, 7, 1), _user(s))
        s.commit()
        assert "hop" not in _uids(retention.select_calls(s, 90, NOW))
        assert "unfilled" not in _uids(retention.select_calls(s, 90, NOW, teams=TEAM))       # form never filled
        assert "unfilled" in _uids(retention.select_calls(s, 90, NOW))                       # without teams: no check
        fu.set_setting(s, fu.SETTING_START, None, _user(s))
        s.commit()


def test_a_call_kept_for_two_reasons_is_counted_once(env):
    Session, *_ = env
    with Session() as s:
        both = _old(s, "both")
        s.add(RatingPick(call_id=both.id, rank=1, day=date(2026, 8, 6), salt="x"))
        _form(s, both, False)
        s.commit()
        assert retention.kept_counts(s, 90, NOW) == (1, 0)
        assert retention.kept_counts(s, 90, NOW, keep_rating_set=False) == (0, 1)


def test_switched_off_rating_keep_erases_the_gold_clip_too(env):
    Session, audio, files, _ = env
    (audio / "goldset").mkdir()
    (audio / "goldset" / "clip_g.wav").write_bytes(b"clip")
    off = RetentionConfig(enabled=True, days=90, keep_rating_set=False)
    with Session() as s:
        g = _old(s, "gold")
        s.add(GoldClip(call_id=g.id, start_seconds=0, seconds=60, file="goldset/clip_g.wav", reference_text="t"))
        s.commit()
        retention.run(s, off, audio, files, NOW)                                   # dry run
        assert (audio / "goldset" / "clip_g.wav").exists()
        retention.run(s, off, audio, files, NOW)
        assert s.scalar(select(func.count()).select_from(GoldClip)) == 0
    assert not (audio / "goldset" / "clip_g.wav").exists()


def test_the_first_real_run_needs_a_recent_dry_run(env):
    Session, audio, files, _ = env
    with Session() as s:
        assert retention.run(s, ON, audio, files, NOW - timedelta(days=10))["dry_run"]     # checked 10 days ago
        assert retention.run(s, ON, audio, files, NOW)["dry_run"]                          # too old: check again
        assert retention.run(s, ON, audio, files, NOW)["dry_run"] is False                 # checked: deletes
        assert retention.run(s, ON, audio, files, NOW + timedelta(days=30))["dry_run"] is False   # keeps deleting
    assert not (audio / "old" / "a.wav").exists()


def test_the_week_a_share_was_sent_in_is_saved_too(env, engine):  # noqa: F811
    Session, audio, files, _ = env
    with engine.begin() as c:
        c.execute(text("TRUNCATE kpi_snapshot RESTART IDENTITY"))
    with Session() as s:
        # the fixture's old calls are on Thu 2026-08-06; this share was sent on Sun 9 Aug (next week)
        call = s.scalar(select(Call).where(Call.uid == "old"))
        u = _user(s)
        s.add(CallShare(call_id=call.id, from_user_id=u, to_user_id=u, note="n", agent_closed_at=NOW,
                        created_at=call.start_time + timedelta(days=3)))
        s.commit()
        r = retention.run(s, ON, audio, files, NOW, teams=TEAM)
        weeks = set(s.scalars(select(KpiSnapshot.start).where(KpiSnapshot.kind == "week")))
    assert weeks == {date(2026, 8, 2), date(2026, 8, 9)} and r["snapshots"] == 18
