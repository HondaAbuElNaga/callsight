"""Follow-up rules (spec 2026-11-15-agent-supervisor, decisions 2-5, 11, 12): needed calls, form deadline, callback
check at the edges, came-back flag, KPI numbers. Real test Postgres (norm_number + LATERAL run in SQL)."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from callsight import followups as fu
from callsight.api import auth
from callsight.config import RIYADH
from callsight.db.models import AppUser, Call, CallFollowup
from test_api import PASSWORD, engine  # noqa: F401  (engine is a fixture)

EXTS = ["104", "116", "119"]
DAY = date(2026, 11, 10)


def at(h: int, m: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, h, m, tzinfo=RIYADH)


@pytest.fixture
def s(engine):  # noqa: F811
    with engine.begin() as c:
        c.execute(text("TRUNCATE call_followup, call_share, app_setting, admin_log, app_user, call_leg, call "
                       "RESTART IDENTITY CASCADE"))
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session() as session:
        yield session


def mk(s, uid, start: datetime, *, direction="inbound", status="answered", agent="119", talk=60, total=None,
       number="0501234567") -> Call:
    c = Call(uid=uid, direction=direction, entry="queue", customer_number=number, team="customer_service",
             agent_ext=agent, start_time=start, end_time=start + timedelta(seconds=talk + 10), talk_seconds=talk,
             total_seconds=total if total is not None else talk + 10, status=status, recording_status="none")
    s.add(c)
    s.flush()
    return c


def user(s, name="ali", role="agent", ext="119") -> AppUser:
    return auth.create_user(s, name, PASSWORD, role, ext, set(EXTS) if role == "agent" else None)


def form(s, c: Call, u: AppUser, *, filled: datetime, solved=False, callback: datetime | None = None,
         no_followup=False) -> CallFollowup:
    f = CallFollowup(call_id=c.id, owner_ext=c.agent_ext, no_followup=no_followup,
                     what_happened=None if no_followup else "x", solved=None if no_followup else solved,
                     responsible_kind=None if solved or no_followup else "customer",
                     expected_at=callback, callback_at=callback, filled_by=u.id, first_filled_at=filled,
                     updated_by=u.id, updated_at=filled)
    s.add(f)
    s.flush()
    return f


def start_on(s, day=DAY, kpi: date | None = None):
    fu.set_setting(s, fu.SETTING_START, day, None)
    if kpi:
        fu.set_setting(s, fu.SETTING_KPI, kpi, None)
    s.flush()


def rows(s, now=None):
    return {r.call.uid: r.as_dict(now or at(12, day=DAY + timedelta(days=5))) for r in
            fu.rows(s, EXTS, DAY, at(0), at(0, day=DAY + timedelta(days=5)), callers=EXTS)}


# ---------- which calls need a form (decision 3) ----------

def test_needed_calls(s):
    start_on(s)
    mk(s, "ok", at(9))
    mk(s, "short", at(9, 5), talk=20)                       # talk must be MORE than 20 s
    mk(s, "just", at(9, 6), talk=21)
    mk(s, "out", at(9, 10), direction="outbound")
    mk(s, "missed", at(9, 20), status="no_answer")
    mk(s, "supervisor", at(9, 30), agent="114")             # not a team agent
    mk(s, "before", at(23, day=DAY - timedelta(days=1)))   # before followup_start
    deleted = mk(s, "deleted", at(10))
    deleted.content_deleted_at = at(11)
    s.flush()
    assert set(rows(s)) == {"ok", "just"}


def test_feature_off_without_start(s):
    c = mk(s, "ok", at(9))
    assert not fu.is_needed(c, EXTS, None)
    assert fu.kpi(s, at(0), at(0, day=DAY + timedelta(days=7)), EXTS, callers=EXTS)["enabled"] is False


# ---------- form deadline (decision 11) ----------

def test_deadline_is_23_or_two_hours_after_a_late_call(s):
    early = mk(s, "a", at(10))
    late = mk(s, "b", at(22, 30))
    assert fu.form_deadline(early) == at(23)
    assert fu.form_deadline(late) == late.end_time + timedelta(hours=2)


def test_form_state_edges(s):
    start_on(s)
    u = user(s)
    on_time, late, missing = mk(s, "on", at(10)), mk(s, "late", at(11)), mk(s, "miss", at(12))
    form(s, on_time, u, filled=at(22, 59), solved=True)
    form(s, late, u, filled=at(23, 1), solved=True)
    got = rows(s)
    assert got["on"]["form_state"] == "on_time"
    assert got["late"]["form_state"] == "late"
    assert got["miss"]["form_state"] == "overdue"
    assert rows(s, now=at(22))["miss"]["form_state"] == "missing"


# ---------- callback check (decisions 2, 4, 5, 12) ----------

def _callback_case(s, cb_start: datetime | None, *, total=30, status="answered", number="+966501234567",
                   agent="104", orig_number="0501234567"):
    start_on(s)
    u = user(s)
    c = mk(s, "orig", at(10), number=orig_number)
    form(s, c, u, filled=at(10, 5), callback=at(14))
    if cb_start is not None:
        mk(s, "cb", cb_start, direction="outbound", status=status, agent=agent, talk=0 if status != "answered" else 20,
           total=total, number=number)
    return rows(s)["orig"]["callback"]


def test_callback_at_59_minutes_after_is_on_time_by_any_colleague(s):
    cb = _callback_case(s, at(14, 59))
    assert cb["status"] == "on_time" and cb["by_ext"] == "104"       # number format +966 vs 05 matched


def test_callback_61_minutes_after_is_late(s):
    assert _callback_case(s, at(15, 1))["status"] == "late"


def test_early_callback_counts(s):
    assert _callback_case(s, at(11))["status"] == "on_time"


def test_unanswered_attempt_counts_from_10_seconds(s):
    assert _callback_case(s, at(14, 10), status="no_answer", total=10)["status"] == "on_time"


def test_callback_answered_comes_from_the_pbx_record(s):
    assert _callback_case(s, at(14, 10))["answered"] is True


def test_callback_not_answered_comes_from_the_pbx_record(s):
    assert _callback_case(s, at(14, 10), status="no_answer", total=15)["answered"] is False


def test_later_answered_callback_counts_as_answered(s):
    start_on(s)
    u = user(s)
    c = mk(s, "orig", at(10), number="0501234567")
    form(s, c, u, filled=at(10, 5), callback=at(14))
    mk(s, "try1", at(14, 5), direction="outbound", status="no_answer", agent="104", talk=0, total=15, number="0501234567")
    mk(s, "try2", at(16), direction="outbound", status="answered", agent="119", talk=30, total=40, number="0501234567")
    cb = rows(s)["orig"]["callback"]
    assert cb["answered"] is True and cb["status"] == "on_time" and cb["by_ext"] == "104"   # first try decides the time


def test_no_callback_call_yet_means_answered_unknown(s):
    cb = _callback_case(s, None)
    assert cb["answered"] is None and cb["status"] in ("waiting", "missed")


def test_unanswered_attempt_of_9_seconds_does_not_count(s):
    assert _callback_case(s, at(14, 10), status="no_answer", total=9)["status"] == "missed"


def test_no_callback_waiting_then_missed(s):
    start_on(s)
    u = user(s)
    c = mk(s, "orig", at(10))
    form(s, c, u, filled=at(10, 5), callback=at(14))
    assert rows(s, now=at(15))["orig"]["callback"]["status"] == "waiting"
    assert rows(s, now=at(15, 1))["orig"]["callback"]["status"] == "missed"


def test_other_number_does_not_count(s):
    assert _callback_case(s, at(14), number="0509999999")["status"] == "missed"


def test_callback_by_a_non_team_extension_does_not_count(s):
    assert _callback_case(s, at(14, 10), agent="114")["status"] == "missed"     # 114 is not in teams.yaml


def test_no_digits_never_match(s):
    start_on(s)
    u = user(s)
    a = mk(s, "a", at(10), number="anonymous")
    form(s, a, u, filled=at(10, 5), solved=True)
    mk(s, "again", at(12), agent="104", number="anonymous")
    assert rows(s)["a"]["came_back"] is False


def test_retention_erased_call_is_left_out(s, tmp_path):
    from callsight import retention
    start_on(s)
    u = user(s)
    a = mk(s, "a", at(10))
    form(s, a, u, filled=at(10, 5), solved=True)
    retention.erase_call_content(s, a, tmp_path, None, None)
    s.flush()
    assert rows(s) == {}


def test_no_number_is_its_own_state(s):
    assert _callback_case(s, at(14), number="", orig_number="")["status"] == "no_number"


def test_solved_or_no_followup_has_no_callback(s):
    start_on(s)
    u = user(s)
    a, b = mk(s, "a", at(10)), mk(s, "b", at(11), number="0507777777")
    form(s, a, u, filled=at(10, 5), solved=True)
    form(s, b, u, filled=at(11, 5), no_followup=True)
    got = rows(s)
    assert got["a"]["callback"] is None and got["b"]["callback"] is None


# ---------- came back after solved (decision 12, show-only) ----------

def test_came_back_within_72_hours_after_solved(s):
    start_on(s)
    u = user(s)
    a = mk(s, "a", at(10))
    form(s, a, u, filled=at(10, 5), solved=True)
    mk(s, "again", at(9, day=DAY + timedelta(days=2)), agent="104")
    assert rows(s)["a"]["came_back"] is True


def test_not_came_back_after_72_hours(s):
    start_on(s)
    u = user(s)
    a = mk(s, "a", at(10))
    form(s, a, u, filled=at(10, 5), solved=True)
    mk(s, "again", at(11, day=DAY + timedelta(days=3)), agent="104")
    assert rows(s)["a"]["came_back"] is False


# ---------- KPIs (decision 6) ----------

def test_kpi_numbers_and_show_only_flag(s):
    start_on(s)
    u = user(s)
    a, b, c = mk(s, "a", at(10)), mk(s, "b", at(11), number="0502222222"), mk(s, "c", at(12), number="0503333333")
    form(s, a, u, filled=at(10, 5), callback=at(14))
    mk(s, "cb", at(14, 30), direction="outbound", agent="119", number="0501234567")
    form(s, b, u, filled=at(23, 30), callback=at(15))           # form late, callback missed
    _ = c                                                          # no form at all -> overdue
    lo, hi = at(0), at(0, day=DAY + timedelta(days=1))
    k = fu.kpi(s, lo, hi, EXTS, callers=EXTS, now=at(12, day=DAY + timedelta(days=2)))
    assert k["enabled"] is True and k["active"] is False
    v = k["agents"]["119"]
    assert (v["forms_due"], v["forms_on_time"], v["callbacks_due"], v["callbacks_on_time"]) == (3, 1, 2, 1)
    assert v["forms_rate"] == pytest.approx(1 / 3) and v["callbacks_rate"] == pytest.approx(0.5)
    assert k["summary"]["forms_due"] == 3
    assert k["agents"]["104"]["forms_rate"] is None


def test_kpi_counts_only_from_the_switch_on_date(s):
    start_on(s, kpi=DAY + timedelta(days=1))
    mk(s, "before", at(10))
    mk(s, "after", at(10, day=DAY + timedelta(days=1)), number="0504444444")
    k = fu.kpi(s, at(0), at(0, day=DAY + timedelta(days=3)), EXTS, callers=EXTS, now=at(12, day=DAY + timedelta(days=3)))
    assert k["active"] is True and k["summary"]["forms_due"] == 1


def test_pending_form_is_not_counted_yet(s):
    start_on(s)
    mk(s, "a", at(10))
    k = fu.kpi(s, at(0), at(0, day=DAY + timedelta(days=1)), EXTS, callers=EXTS, now=at(12))
    assert k["summary"]["forms_due"] == 0


# ---------- form validation (R3) ----------

def test_validate_rules(s):
    start_on(s)
    c = mk(s, "a", at(10))
    now = at(11)
    sup = user(s, "boss", "supervisor", "114")
    ok = {"what_happened": "x", "solved": False, "responsible_kind": "user", "responsible_user_id": sup.id,
          "expected_at": at(16), "callback_at": at(15)}
    assert fu.validate(s, ok, c, now)["callback_at"] == at(15)
    for bad in ({**ok, "what_happened": " "}, {**ok, "solved": None}, {**ok, "responsible_kind": "boss"},
                {**ok, "responsible_user_id": 999}, {**ok, "callback_at": at(10, 30)},
                {**ok, "callback_at": now + timedelta(days=15)}, {**ok, "expected_at": None},
                {**ok, "callback_at": datetime(2026, 11, 10, 15)}):
        with pytest.raises(fu.FormError):
            fu.validate(s, bad, c, now)
    assert fu.validate(s, {"no_followup": True, "what_happened": "ignored"}, c, now)["what_happened"] is None
    sup.active = False
    with pytest.raises(fu.FormError):
        fu.validate(s, ok, c, now)


# ---------- steps of one call (spec 2026-11-15-followup-stages) ----------

def _open_case(s, *, cb1: datetime | None = at(14, 10)):
    """Call at 10:00, form not solved with callback 14:00, a team callback at `cb1` (104)."""
    start_on(s)
    u = user(s)
    c = mk(s, "orig", at(10))
    form(s, c, u, filled=at(10, 5), callback=at(14))
    if cb1 is not None:
        mk(s, "cb1", cb1, direction="outbound", agent="104", number="+966501234567")
    s.commit()
    return c, u


def _row(s, c):
    return fu.one(s, c, EXTS, DAY)


def _note(s, c, u, now, *, rnd=2, solved=False, callback=None):
    data = {"round": rnd, "what_happened": "called back", "solved": solved}
    if not solved:
        data |= {"responsible_kind": "customer", "expected_at": callback, "callback_at": callback}
    return fu.add_note(s, c, data, u, EXTS, now)


def test_steps_before_and_after_the_form(s):
    start_on(s)
    u = user(s)
    a, b, c = mk(s, "a", at(10)), mk(s, "b", at(11), number="0502222222"), mk(s, "c", at(12), number="0503333333")
    form(s, b, u, filled=at(11, 5), solved=True)
    form(s, c, u, filled=at(12, 5), no_followup=True)
    assert _row(s, a).step(at(22, 59)) == "required"
    assert _row(s, a).step(at(23, 1)) == "overdue"
    assert _row(s, b).step(at(13)) == "solved_first"
    assert _row(s, c).step(at(13)) == "no_followup"


def test_waiting_then_missed_then_late_callback_needs_a_note(s):
    c, _ = _open_case(s, cb1=None)
    assert _row(s, c).step(at(15)) == "waiting_callback"
    assert _row(s, c).step(at(15, 1)) == "callback_missed"
    mk(s, "late", at(16), direction="outbound", agent="119")
    d = _row(s, c).as_dict(at(16, 5))
    assert d["step"] == "note_needed" and d["callback"]["status"] == "late" and d["round"] == 1


def test_note_needed_after_the_callback_with_its_deadline(s):
    c, _ = _open_case(s)
    d = _row(s, c).as_dict(at(14, 30))
    assert d["step"] == "note_needed" and d["note_deadline"] == at(23).isoformat()
    assert d["callback"]["by_ext"] == "104" and d["callback"]["answered"] is True


def test_note_solved_ends_as_solved_after(s):
    c, u = _open_case(s)
    _note(s, c, u, at(15), solved=True)
    d = _row(s, c).as_dict(at(15, 5))
    assert d["step"] == "solved_after" and d["round"] == 2 and d["callback"] is None
    h = d["history"]
    assert [x["round"] for x in h] == [1, 2] and h[0]["callback_status"] == "on_time"
    assert h[0]["callback_call"]["by_ext"] == "104" and h[1]["solved"] is True


def test_note_not_solved_opens_round_3_and_old_calls_do_not_count(s):
    c, u = _open_case(s)
    mk(s, "before_note", at(14, 50), direction="outbound", agent="119")      # before the note: not round 2's call
    _note(s, c, u, at(15), callback=at(18))
    r = _row(s, c)
    assert r.step(at(17)) == "waiting_callback" and r.as_dict(at(17))["round"] == 2
    assert r.step(at(19, 1)) == "callback_missed"
    mk(s, "cb2", at(18, 20), direction="outbound", agent="119")
    r = _row(s, c)
    d = r.as_dict(at(18, 30))
    assert d["step"] == "note_needed" and d["callback"]["status"] == "on_time" and d["callback"]["round"] == 2
    _note(s, c, u, at(18, 40), rnd=3, solved=True)
    d = _row(s, c).as_dict(at(19))
    assert d["step"] == "solved_after" and [x["callback_status"] for x in d["history"]] == ["on_time", "on_time", None]


def test_note_only_when_needed_and_for_the_shown_round(s):
    c, u = _open_case(s, cb1=None)
    with pytest.raises(fu.NoteConflict):
        _note(s, c, u, at(13), solved=True)                         # no callback yet
    mk(s, "cb1", at(14, 10), direction="outbound", agent="104")
    s.commit()
    with pytest.raises(fu.NoteConflict):
        _note(s, c, u, at(15), rnd=3, solved=True)                  # wrong round (old page)
    _note(s, c, u, at(15), solved=True)
    with pytest.raises(fu.NoteConflict):
        _note(s, c, u, at(15, 1), solved=True)                      # second click
    with pytest.raises(fu.FormError):
        _note(s, *_open_case_again(s), at(15), callback=at(14, 30))  # callback time in the past


def _open_case_again(s):
    u = s.query(AppUser).filter_by(username="ali").one()
    c = mk(s, "orig2", at(9), number="0507777777")
    form(s, c, u, filled=at(9, 5), callback=at(13))
    mk(s, "cb_o2", at(13, 5), direction="outbound", agent="119", number="0507777777")
    s.commit()
    return c, u


def test_note_deadline_edges(s):
    c, u = _open_case(s, cb1=at(21, 30))        # callback call ends 21:31:10 -> note due 23:31:10
    r = _row(s, c)
    assert r.note_states(at(23, 31)) == ["pending"] and r.note_states(at(23, 32)) == ["overdue"]
    _note(s, c, u, at(23, 31), solved=True)
    assert _row(s, c).note_states(at(23, 59)) == ["on_time"]


def test_late_note_counts_late(s):
    c, u = _open_case(s)
    _note(s, c, u, at(23, 30), solved=True)
    assert _row(s, c).note_states(at(23, 59)) == ["late"]


def test_kpi_stage_numbers(s):
    c, u = _open_case(s)                                    # round 1 on time, note on time, round 2 missed -> open
    _note(s, c, u, at(15), callback=at(18))
    first = mk(s, "first", at(11), number="0502222222")
    form(s, first, u, filled=at(11, 5), solved=True)
    after = mk(s, "after", at(12), number="0503333333")
    form(s, after, u, filled=at(12, 5), callback=at(13))
    mk(s, "cb_after", at(13, 10), direction="outbound", agent="119", number="0503333333")
    s.commit()
    fu.add_note(s, after, {"round": 2, "what_happened": "done", "solved": True}, u, EXTS, at(13, 30))
    nofu = mk(s, "nofu", at(12, 30), number="0504444444")
    form(s, nofu, u, filled=at(12, 35), no_followup=True)
    k = fu.kpi(s, at(0), at(0, day=DAY + timedelta(days=1)), EXTS, callers=EXTS, now=at(12, day=DAY + timedelta(days=1)))
    v = k["agents"]["119"]
    assert (v["answered_forms"], v["solved_first"], v["solved_after"], v["open"]) == (3, 1, 1, 1)
    assert v["first_rate"] == pytest.approx(1 / 3) and v["avg_rounds"] == 1
    assert (v["callbacks_due"], v["callbacks_on_time"]) == (3, 2)       # orig r1 on time, orig r2 missed, after r1
    assert (v["notes_due"], v["notes_on_time"]) == (2, 2)
    assert k["summary"]["solved_after"] == 1


def test_retention_erases_the_notes(s, tmp_path):
    from callsight import retention
    from callsight.db.models import CallFollowupNote
    c, u = _open_case(s)
    _note(s, c, u, at(15), solved=True)
    retention.erase_call_content(s, c, tmp_path, None, None)
    s.commit()
    assert s.query(CallFollowupNote).count() == 0


def test_pdf_table_has_the_stage_columns(s):
    from callsight.reports import pdf
    c, u = _open_case(s)
    _note(s, c, u, at(15), solved=True)
    k = fu.kpi(s, at(0), at(0, day=DAY + timedelta(days=1)), EXTS, callers=EXTS, now=at(12, day=DAY + timedelta(days=1)))
    html = pdf.followup_table(k, [{"ext": "119", "name": "علي"}])
    assert "حُلّت بعد المتابعة" in html and "نسبة الحل من أول مكالمة" in html and "1.0" in html


# ---------- review fixes (2026-11-15) ----------

def test_call_before_the_form_is_not_the_promised_callback(s):
    start_on(s)
    u = user(s)
    c = mk(s, "orig", at(10))
    mk(s, "early", at(10, 5), direction="outbound", agent="119")            # line dropped, rang back at once
    form(s, c, u, filled=at(10, 30), callback=at(12))                        # then the form: not solved, 12:00
    s.commit()
    r = _row(s, c)
    assert r.step(at(11)) == "waiting_callback" and r.step(at(13, 1)) == "callback_missed"
    mk(s, "real", at(12, 10), direction="outbound", agent="104")
    d = _row(s, c).as_dict(at(12, 20))
    assert d["step"] == "note_needed" and d["callback"]["by_ext"] == "104" and d["callback"]["status"] == "on_time"


def test_history_keeps_answered_for_finished_rounds(s):
    c, u = _open_case(s)
    _note(s, c, u, at(15), solved=True)
    assert _row(s, c).as_dict(at(15, 5))["history"][0]["callback_call"]["answered"] is True


def test_kpi_per_agent_split_and_no_number_is_not_open(s):
    start_on(s)
    u, sara = user(s), user(s, "sara", ext="104")
    a = mk(s, "a", at(10))
    form(s, a, u, filled=at(10, 5), solved=True)
    b = mk(s, "b", at(11), agent="104", number="0502222222")
    form(s, b, sara, filled=at(11, 5), callback=at(13))
    hidden = mk(s, "hidden", at(12), agent="104", number="")
    form(s, hidden, sara, filled=at(12, 5), callback=at(14))
    k = fu.kpi(s, at(0), at(0, day=DAY + timedelta(days=1)), EXTS, callers=EXTS, now=at(12, day=DAY + timedelta(days=1)))
    a119, a104, tot = k["agents"]["119"], k["agents"]["104"], k["summary"]
    assert (a119["answered_forms"], a119["solved_first"], a119["open"]) == (1, 1, 0)
    assert (a104["answered_forms"], a104["solved_first"], a104["open"]) == (2, 0, 1)      # "hidden" = no_number
    for key in ("answered_forms", "solved_first", "open", "callbacks_due"):
        assert tot[key] == a119[key] + a104[key]
