"""Rules editor (spec 2026-11-17-rules-editor): word lists + topics in the DB, first fill from YAML, DB wins after,
normalisation, duplicates, limits, admin-only edits, history rows, test-a-sentence, the worker cache, re-check.
DB tests run on TEST_DATABASE_URL (a *_test database); skipped when it isn't reachable."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from callsight.analysis import flags, llm, rulestore as rs
from callsight.analysis.settings import load_rules, load_topics
from callsight.api import auth
from callsight.api.main import ApiConfig, create_app
from callsight.config import RIYADH
from callsight.db.models import AdminLog, Call, CallFlag, RuleChange, RuleList, Transcript, TranscriptSegment
from test_api import PASSWORD, SECRET, engine  # noqa: F401  (engine is a fixture)

RULES_YAML = """flags:
  bad_words:
    - غَبِيّ
    - غبي
    - حمار
  escalation:
    - أبي المدير
  competitor:
    - معهد ثاني
competitor_rule:
  word: معهد
  own: [السعودي]
  ignore_next: [اليوم]
  label: معهد غير المعهد السعودي
"""
TOPICS_YAML = "topics:\n  inquiry: استفسار\n  billing: فاتورة\n  other: أخرى\n"


@pytest.fixture
def files(tmp_path, monkeypatch):
    rp, tp = tmp_path / "rules.yaml", tmp_path / "topics.yaml"
    rp.write_text(RULES_YAML, encoding="utf-8")
    tp.write_text(TOPICS_YAML, encoding="utf-8")
    monkeypatch.setenv("RULES_FILE", str(rp))
    monkeypatch.setenv("TOPICS_FILE", str(tp))
    rs.clear_cache()
    return rp, tp


# ---------- no DB ----------

def test_norm_word_folds_like_the_matcher_and_checks_limits():
    assert rs.norm_word("  غَبِيّ ", "bad_words") == "غبي"
    assert rs.norm_word("أبي  المدير", "escalation") == "ابي المدير"
    assert rs.norm_word("قليلة", "bad_words") == "قليله"                  # ة -> ه, as the transcript search
    for bad in ("", "   ", "abc1", "غبي!", "<b>", "ا" * 41):
        with pytest.raises(rs.RuleError):
            rs.norm_word(bad, "bad_words")
    assert rs.norm_word("ا" * 40, "bad_words") == "ا" * 40
    with pytest.raises(rs.RuleError):
        rs.norm_word("المعهد السعودي", rs.OWN)                             # one word in the competitor-rule lists


def test_yaml_lists_normalised_without_duplicates(files):
    rp, tp = files
    lists = rs.yaml_lists(rp, tp)
    assert lists["bad_words"] == ["غبي", "حمار"]                          # غَبِيّ and غبي are the same word
    assert lists["escalation"] == ["ابي المدير"]
    assert lists[rs.OWN] == ["السعودي"] and lists[rs.IGNORE] == ["اليوم"]
    assert lists[rs.TOPICS] == [["inquiry", "استفسار"], ["billing", "فاتورة"], ["other", "أخرى"]]


def test_no_db_rows_means_exactly_the_yaml(files):
    rp, tp = files
    rules, topics = rs.apply(load_rules(rp), load_topics(tp), {}, rp)
    assert rules == load_rules(rp) and topics == load_topics(tp)


def test_match_sentence_uses_the_matcher():
    rules = rs.apply(load_rules_text(), {"other": "x"}, {"bad_words": ["حمار"], "escalation": ["ابي المدير"],
                                                         "competitor": [], rs.OWN: ["السعودي"], rs.IGNORE: []},
                     _tmp_rules())[0]
    hits = rs.match_sentence("يا حمار، أبي المدير الحين. سجلت في معهد الخليج", rules)
    assert {(h["kind"], h["phrase"]) for h in hits} == {("bad_words", "حمار"), ("escalation", "ابي المدير"),
                                                         ("competitor", "معهد غير المعهد السعودي")}
    assert rs.match_sentence("المعهد السعودي ممتاز", rules) == []


def _tmp_rules():
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / "rules.yaml"
    p.write_text(RULES_YAML, encoding="utf-8")
    return p


def load_rules_text():
    return load_rules(_tmp_rules())


# ---------- DB ----------

@pytest.fixture
def Session(engine, files):  # noqa: F811
    with engine.begin() as c:
        c.execute(text("TRUNCATE rule_change, rule_list, admin_log, call_flag, transcript_segment, transcript, call, "
                       "app_user RESTART IDENTITY CASCADE"))
    return sessionmaker(engine, expire_on_commit=False)


def test_first_fill_from_yaml_then_db_wins(Session, files):
    rp, tp = files
    with Session() as s:
        assert sorted(rs.ensure_seeded(s)) == sorted(rs.LIST_NAMES)
        s.commit()
        assert rs.ensure_seeded(s) == []                                  # once only
        hist = s.scalars(select(RuleChange)).all()
        assert len(hist) == len(rs.LIST_NAMES) and all(h.user_id is None for h in hist)
        assert next(h for h in hist if h.list_name == "bad_words").added == ["غبي", "حمار"]
        rp.write_text(RULES_YAML.replace("- حمار", "- كلب"), encoding="utf-8")    # YAML edited after the fill
        tp.write_text(TOPICS_YAML.replace("فاتورة", "دفع"), encoding="utf-8")
        rules, topics = rs.current(s)
        assert rules.flags["bad_words"] == ("غبي", "حمار")                # the DB wins
        assert topics == {"inquiry": "استفسار", "billing": "فاتورة", "other": "أخرى"}
        assert rs.current_topics(s) == topics


def test_seeded_topics_keep_the_prompt_version(Session, files):
    """Deploy must not re-analyse every call: the same topics give the same prompt version."""
    _, tp = files
    with Session() as s:
        before = llm.prompt_version(rs.current_topics(s))
        rs.ensure_seeded(s)
        s.commit()
        assert llm.prompt_version(rs.current_topics(s)) == before == llm.prompt_version(load_topics(tp))


def _admin(s):
    return auth.create_user(s, "root", PASSWORD, "admin")


def test_change_words_history_duplicates_and_limits(Session):
    with Session() as s:
        u = _admin(s)
        items = rs.change_words(s, "bad_words", u.id, ["كَلْب", "تافهة"], ["حمار"], "طلب المدير")
        s.commit()
        assert items == ["غبي", "كلب", "تافهه"]
        h = s.scalars(select(RuleChange).where(RuleChange.user_id == u.id)).one()
        assert (h.list_name, h.added, h.removed, h.reason) == ("bad_words", ["كلب", "تافهه"], ["حمار"], "طلب المدير")
        for add, remove, why in ((["كلب"], [], "already"), (["غَبي"], [], "already (normalised)"),
                                 (["زفت", "زفت"], [], "twice in one save"), ([], ["مش موجود"], "unknown"),
                                 ([], [], "nothing")):
            with pytest.raises(rs.RuleError):
                rs.change_words(s, "bad_words", u.id, add, remove, "سبب " + why)
            s.rollback()
        with pytest.raises(rs.RuleError):
            rs.change_words(s, "bad_words", u.id, ["زفت"], [], "")       # reason needed
        s.rollback()
        with pytest.raises(rs.RuleError):
            rs.change_words(s, "nope", u.id, ["زفت"], [], "سبب")
        s.rollback()
        abc = "بتثجحخدذرزسشصضطظعغفقكلمن"
        many = ["ك" + a + b for a in abc for b in abc][:rs.MAX_ITEMS]     # 500 new + 1 already there = 501
        with pytest.raises(rs.RuleError, match="at most 500"):
            rs.change_words(s, "competitor", u.id, many, [], "too many")
        s.rollback()
        assert s.get(RuleList, "competitor").items == ["معهد ثاني"]
        assert s.scalar(select(RuleChange.id).where(RuleChange.user_id == u.id).order_by(RuleChange.id.desc())
                        .limit(1)) == h.id                                # refused edits leave no history


def test_change_topics_keeps_other_and_changes_prompt_version(Session):
    with Session() as s:
        u = _admin(s)
        pv = llm.prompt_version(rs.current_topics(s))
        with pytest.raises(rs.RuleError):
            rs.change_topics(s, u.id, {}, ["other"], "remove other")
        s.rollback()
        for bad in ({"Bad-Key": "x"}, {"ok_key": ""}, {"ok_key": "ف" * 41}, {"ok_key": "فاتورة"}):
            with pytest.raises(rs.RuleError):
                rs.change_topics(s, u.id, bad, [], "bad topic")
            s.rollback()
        items = rs.change_topics(s, u.id, {"training": "تدريب", "billing": "فاتورة / دفع"}, ["inquiry"], "new list")
        s.commit()
        assert items == [["billing", "فاتورة / دفع"], ["other", "أخرى"], ["training", "تدريب"]]
        h = s.scalars(select(RuleChange).where(RuleChange.user_id == u.id)).one()
        assert sorted(h.removed) == ["billing: فاتورة", "inquiry: استفسار"]
        assert sorted(h.added) == ["billing: فاتورة / دفع", "training: تدريب"]
        assert llm.prompt_version(rs.current_topics(s)) != pv


def make_call(s, texts, start) -> Call:
    c = Call(uid=f"r{start.timestamp()}", direction="inbound", entry="queue", start_time=start,
             end_time=start + timedelta(minutes=2), talk_seconds=60, total_seconds=70, status="answered",
             agent_ext="119", recording_status="none")
    s.add(c)
    s.flush()
    t = Transcript(call_id=c.id, model="whisper-large-v3", status="done", text=" ".join(texts), audio_seconds=30,
                   finished_at=start, dropped_windows=0)
    s.add(t)
    s.flush()
    s.add_all(TranscriptSegment(transcript_id=t.id, seq=i, start_seconds=i * 29.5, end_seconds=(i + 1) * 29.5,
                                text=x) for i, x in enumerate(texts))
    s.commit()
    return c


def test_word_added_on_the_page_flags_the_next_call_and_worker_cache(Session, files):
    rp, tp = files
    with Session() as s:
        u = _admin(s)
        c = make_call(s, ["والله انك زفت"], datetime.now(RIYADH))
        t = s.scalars(select(Transcript).where(Transcript.call_id == c.id)).one()
        rules, _ = rs.cached(Session, rp, tp)                             # the worker's view (1-minute cache)
        assert flags.store(s, c.id, t, rules) == 0
        rs.change_words(s, "bad_words", u.id, ["زفت"], [], "new word")
        s.commit()
        assert rs.cached(Session, rp, tp)[0] is rules                     # still cached (< 1 minute)
        fresh, _ = rs.cached(Session, rp, tp, max_age=0)                  # after the minute
        assert fresh.version != rules.version
        assert flags.store(s, c.id, t, fresh) == 1
        s.commit()
        f = s.scalars(select(CallFlag)).one()
        assert (f.kind, f.phrase, f.rules_version) == ("bad_words", "زفت", fresh.version)


def test_recheck_last_7_days_only(Session, files, monkeypatch):
    from callsight import app as wiring
    from callsight import jobs
    with Session() as s:
        u = _admin(s)
        new = make_call(s, ["ابي المدير الحين"], datetime.now(RIYADH) - timedelta(days=2))
        old = make_call(s, ["ابي المدير الحين"], datetime.now(RIYADH) - timedelta(days=9))
        rs.change_words(s, "escalation", u.id, [], ["ابي المدير"], "too many hits")
        rs.change_words(s, "bad_words", u.id, ["الحين"], [], "test word")
        s.commit()
        for c in (new, old):                                              # flags from the old lists
            s.add(CallFlag(call_id=c.id, transcript_id=0, kind="escalation", phrase="ابي المدير", start_seconds=0,
                           end_seconds=1, rules_version="r-old"))
        s.commit()
    monkeypatch.setattr(wiring, "session_factory", lambda: Session)
    r = jobs.rules_recheck.run(7)
    assert r["calls"] == 1 and r["days"] == 7
    with Session() as s:
        got = {(f.call_id, f.kind, f.phrase) for f in s.scalars(select(CallFlag))}
    assert got == {(new.id, "bad_words", "الحين"), (old.id, "escalation", "ابي المدير")}


# ---------- API ----------

@pytest.fixture
def api(Session):
    sent = []
    with Session() as s:
        auth.create_user(s, "root", PASSWORD, "admin")
        auth.create_user(s, "boss", PASSWORD, "supervisor", "114")
        auth.create_user(s, "ali", PASSWORD, "agent", "119", {"119": "customer_service"})
    app = create_app(Session, ApiConfig(secret=SECRET, audio_dir=__import__("pathlib").Path("."),
                                        rules_recheck=sent.append))
    return TestClient(app), Session, sent


def as_(client, user):
    client.cookies.clear()
    assert client.post("/api/login", json={"username": user, "password": PASSWORD}).status_code == 200
    return client


def test_api_admin_edits_supervisor_reads_agent_nothing(api):
    client, Session, _ = api
    c = as_(client, "boss")
    page = c.get("/api/rules").json()                                     # first open fills the lists
    assert page["can_edit"] is False and page["lists"]["bad_words"] == ["غبي", "حمار"]
    assert page["topics"][-1] == ["other", "أخرى"] and page["limits"]["items"] == 500
    assert c.post("/api/rules/lists/bad_words", json={"add": ["زفت"], "reason": "test"}).status_code == 403
    assert c.post("/api/rules/topics", json={"set": {"x": "y"}, "reason": "test"}).status_code == 403
    assert c.post("/api/rules/recheck").status_code == 403
    assert c.post("/api/rules/test", json={"text": "يا حمار"}).json()["hits"] == [
        {"kind": "bad_words", "phrase": "حمار"}]
    assert c.get("/api/rules/history").status_code == 200
    a = as_(client, "ali")
    for url in ("/api/rules", "/api/rules/history"):
        assert a.get(url).status_code == 403
    assert a.post("/api/rules/test", json={"text": "x"}).status_code == 403
    r = as_(client, "root")
    res = r.post("/api/rules/lists/bad_words", json={"add": ["زِفت"], "remove": ["حمار"], "reason": "manager asked"})
    assert res.status_code == 200 and res.json()["items"] == ["غبي", "زفت"]
    dup = r.post("/api/rules/lists/bad_words", json={"add": ["زفت"], "reason": "again"})
    assert dup.status_code == 422 and "already" in dup.json()["detail"]
    assert r.post("/api/rules/lists/nope", json={"add": ["زفت"], "reason": "x y z"}).status_code == 404
    assert r.post("/api/rules/lists/bad_words", json={"add": ["كلب"], "reason": "x", "extra": 1}).status_code == 422
    t = r.post("/api/rules/topics", json={"set": {"training": "تدريب"}, "reason": "new topic"})
    assert t.status_code == 200 and ["training", "تدريب"] in t.json()["items"]
    assert r.post("/api/rules/topics", json={"remove": ["other"], "reason": "x y z"}).status_code == 422
    hist = r.get("/api/rules/history").json()
    assert hist[0]["list"] == "topics" and hist[0]["user"] == "root"
    assert hist[1]["added"] == ["زفت"] and hist[1]["removed"] == ["حمار"] and hist[1]["reason"] == "manager asked"
    assert any(h["user"] is None and h["reason"].startswith("first fill") for h in hist)
    with Session() as s:
        assert [x.action for x in s.scalars(select(AdminLog).order_by(AdminLog.id))] == ["rules_change"] * 2


def test_api_recheck_queued_once_per_5_minutes(api):
    client, Session, sent = api
    r = as_(client, "root")
    assert r.post("/api/rules/recheck").json() == {"queued": True, "days": 7}
    assert sent == [7]
    assert r.post("/api/rules/recheck").status_code == 429
    assert sent == [7]


def test_api_key_never_edits_rules(api):
    """An ERP key acting as the admin may read, never change the lists or start a re-check."""
    client, Session, sent = api
    r = as_(client, "root")
    key = r.post("/api/admin/api-clients", json={"name": "ERP"}).json()["key"]
    client.post("/api/logout")
    client.cookies.clear()
    h = {auth.API_KEY_HEADER: key, auth.ACT_AS_HEADER: "root"}
    assert client.get("/api/rules", headers=h).status_code == 200
    assert client.post("/api/rules/lists/bad_words", json={"add": ["زفت"], "reason": "by key"},
                       headers=h).status_code == 403
    assert client.post("/api/rules/topics", json={"set": {"x": "y"}, "reason": "by key"}, headers=h).status_code == 403
    assert client.post("/api/rules/recheck", headers=h).status_code == 403 and sent == []


def test_competitor_rule_lists_match_like_the_matcher(Session):
    with Session() as s:
        u = _admin(s)
        with pytest.raises(rs.RuleError, match="ال"):
            rs.change_words(s, rs.IGNORE, u.id, ["يوم"], [], "no al")       # never checked by the matcher
        s.rollback()
        with pytest.raises(rs.RuleError, match="already"):
            rs.change_words(s, rs.OWN, u.id, ["سعودي"], [], "same as السعودي")
        s.rollback()
        rs.change_words(s, rs.IGNORE, u.id, ["الخليج"], [], "not a competitor here")
        s.commit()
        rules, _ = rs.current(s)
        assert rs.match_sentence("سجلت في معهد الخليج", rules) == []


def test_api_recheck_send_failure_does_not_block(Session):
    calls = []

    def broken(days):
        calls.append(days)
        if len(calls) == 1:
            raise RuntimeError("redis down")
    with Session() as s:
        auth.create_user(s, "root", PASSWORD, "admin")
    client = TestClient(create_app(Session, ApiConfig(secret=SECRET, audio_dir=__import__("pathlib").Path("."),
                                                      rules_recheck=broken)))
    r = as_(client, "root")
    assert r.post("/api/rules/recheck").status_code == 503
    assert r.post("/api/rules/recheck").status_code == 200 and calls == [7, 7]


def test_api_test_sentence_too_long_never_echoed(api):
    client, *_ = api
    r = as_(client, "boss").post("/api/rules/test", json={"text": "سر" * 300})
    assert r.status_code == 422 and "سر" not in r.text


def test_worker_rereads_lists_for_a_newer_prompt_version(Session, files, monkeypatch):
    """Topics edited: a task queued with the new prompt version is never called "outdated" by a stale cache."""
    from callsight import app as wiring
    from callsight.analysis import tasks
    monkeypatch.setattr(wiring, "session_factory", lambda: Session)
    with Session() as s:
        u = _admin(s)
        rs.ensure_seeded(s)
        s.commit()
        old = tasks.deps_from_env().prompt_version                        # cached now
        rs.change_topics(s, u.id, {"training": "تدريب"}, [], "new topic")
        s.commit()
        new = llm.prompt_version(rs.current_topics(s))
    assert tasks.deps_from_env().prompt_version == old != new           # still cached ...
    assert tasks.deps_from_env(fresh=True).prompt_version == new         # ... the task asks for a fresh read


def test_swap_labels_and_readd_in_one_save(Session):
    with Session() as s:
        u = _admin(s)
        items = rs.change_topics(s, u.id, {"inquiry": "فاتورة", "billing": "استفسار"}, [], "swap")
        assert ["inquiry", "فاتورة"] in items and ["billing", "استفسار"] in items
        assert rs.change_words(s, "bad_words", u.id, ["حمار"], ["حمار"], "same word") == ["غبي", "حمار"]
