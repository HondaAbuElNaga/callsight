"""Rating page + call-detail analysis API (spec 2026-11-14-call-analysis): staff only, blind omission, progress,
pre-fill bookkeeping, input checks. Runs on TEST_DATABASE_URL; skipped when it isn't reachable."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from callsight.analysis import blindset
from callsight.api import auth
from callsight.api.main import ApiConfig, create_app
from callsight.db.models import (BlindSet, CallAnalysis, CallAudioMetrics, CallFlag, SatisfactionLabel,
                                 TranscriptAccess)
from test_call_analysis import PV, TOPICS, engine, make_call  # noqa: F401  (engine is a fixture)

SECRET = "z" * 40
PASSWORD = "correct-horse-battery"
TEAM_EXTS = {"119": "customer_service", "104": "customer_service"}


@pytest.fixture
def env(engine, tmp_path):  # noqa: F811
    with engine.begin() as c:
        c.execute(text("TRUNCATE call_analysis, call_flag, call_audio_metrics, satisfaction_label, blind_set, "
                       "transcript_access, transcript_segment, transcript, recording_play, call, app_user "
                       "RESTART IDENTITY CASCADE"))
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session() as s:
        calls = [make_call(s, agent="119" if i % 2 else "104") for i in range(4)]
        for i, c in enumerate(calls):
            s.add(CallAnalysis(call_id=c.id, model="qwen2.5:7b", prompt_version=PV, status="done",
                               summary=f"<b>ملخص {i}</b>", topic="complaint", action_items=["متابعة"],
                               llm_score=3 + i, reason="سبب", escalation=i == 0))
        s.add(CallFlag(call_id=calls[0].id, transcript_id=1, kind="escalation", phrase="بشتكي", start_seconds=29.5,
                       end_seconds=59, rules_version="r-x"))
        s.add(CallAudioMetrics(call_id=calls[0].id, metrics_version="m", audio_seconds=59, silence_seconds=12,
                               silence_share=0.2, longest_silence_s=12, silences=1, long_silences=1))
        s.commit()
        ids = [c.id for c in calls]
        auth.create_user(s, "boss", PASSWORD, "supervisor", "114")
        auth.create_user(s, "admin", PASSWORD, "admin")
        auth.create_user(s, "ali", PASSWORD, "agent", "119", TEAM_EXTS)
    app = create_app(Session, ApiConfig(secret=SECRET, audio_dir=tmp_path, topics=lambda: TOPICS))
    return TestClient(app), Session, ids


def login(client, user="boss"):
    client.cookies.clear()
    assert client.post("/api/login", json={"username": user, "password": PASSWORD}).status_code == 200


def freeze(Session, blind_ids):
    with Session() as s:
        s.add_all(BlindSet(call_id=cid, rank=i, agent_ext="119", sampler_version="v", salt="s")
                  for i, cid in enumerate(blind_ids, 1))
        s.commit()


def test_agents_get_nothing(env):
    client, _, ids = env
    login(client, "ali")
    own = ids[1]                                                # agent 119's own call
    assert client.get(f"/api/calls/{own}/analysis").status_code == 403
    assert client.get("/api/rating/queue").status_code == 403
    assert client.get(f"/api/rating/calls/{own}").status_code == 403
    assert client.put(f"/api/rating/calls/{own}", json={"score": 5}).status_code == 403
    detail = client.get(f"/api/calls/{own}").json()             # call detail holds no S5 data for anyone
    assert not {"analysis", "estimate", "flags", "silence", "summary", "llm_score"} & set(detail)


def test_no_estimate_anywhere_before_the_blind_set_is_frozen(env):
    client, _, ids = env
    login(client)
    d = client.get(f"/api/calls/{ids[0]}/analysis").json()
    assert d["estimate"] is None and d["estimate_hidden"] == "not_frozen"
    assert d["analysis"]["summary"] == "<b>ملخص 0</b>"            # plain string: the page renders it as text
    assert d["analysis"]["topic_label"] == "شكوى" and d["flags"][0]["start"] == 29.5
    assert d["silence"]["long_count"] == 1
    r = client.get(f"/api/rating/calls/{ids[0]}").json()
    assert r["estimate"] is None and r["analysis"]["escalation"] is True


def test_blind_calls_never_show_the_estimate_on_the_rating_page(env):
    client, Session, ids = env
    freeze(Session, [ids[0]])
    login(client)
    blind = client.get(f"/api/rating/calls/{ids[0]}").json()
    assert blind["blind"] is True and blind["estimate"] is None and "llm_score" not in str(blind)
    assert blind["analysis"] is None and "ملخص" not in str(blind) and blind["locked"] is False   # no summary/escalation
    detail = client.get(f"/api/calls/{ids[0]}/analysis").json()                 # call detail: nothing either,
    assert detail["analysis"] is None and detail["estimate"] is None            # until this user has rated it
    assert detail["flags"] and detail["silence"]                                # rule matches + silence: not LLM
    other = client.get(f"/api/rating/calls/{ids[1]}").json()
    assert other["blind"] is False and other["estimate"]["score"] == 4
    assert "ليس لتقييم الموظفين" in other["estimate"]["label"]
    assert client.get(f"/api/calls/{ids[0]}/analysis").json()["estimate_hidden"] == "blind"
    assert client.put(f"/api/rating/calls/{ids[0]}", json={"score": 6}).status_code == 200
    after = client.get(f"/api/rating/calls/{ids[0]}").json()
    assert after["estimate"] is None and after["analysis"] is None and after["locked"] is True   # still hidden here
    detail = client.get(f"/api/calls/{ids[0]}/analysis").json()                 # rated: call detail ok
    assert detail["estimate"]["score"] == 3 and detail["analysis"]["summary"] == "<b>ملخص 0</b>"
    login(client, "admin")                                      # another rater: still hidden for them
    detail = client.get(f"/api/calls/{ids[0]}/analysis").json()
    assert detail["estimate"] is None and detail["analysis"] is None


def test_blind_label_is_final(env):
    """After rating a blind call its rater may see the estimate (call detail): the label can't be changed then."""
    client, Session, ids = env
    freeze(Session, [ids[0]])
    login(client)
    assert client.put(f"/api/rating/calls/{ids[0]}", json={"score": 4, "note": "أول"}).status_code == 200
    r = client.put(f"/api/rating/calls/{ids[0]}", json={"score": 3})            # = the LLM estimate, now visible
    assert r.status_code == 409 and r.json()["detail"] == "blind-set rating is final"
    assert client.put(f"/api/rating/calls/{ids[0]}", json={"score": 4, "note": "ثاني"}).status_code == 409
    with Session() as s:
        lab = s.scalars(select(SatisfactionLabel)).one()
    assert (lab.score, lab.note, lab.prefill, lab.blind) == (4, "أول", None, True)
    login(client, "admin")                                      # another rater still gives their own blind label
    assert client.put(f"/api/rating/calls/{ids[0]}", json={"score": 6}).status_code == 200


def test_reading_the_analysis_is_audited(env):
    client, Session, ids = env
    freeze(Session, [ids[0]])
    login(client)

    def kinds():
        with Session() as s:
            return [(a.call_id, a.kind) for a in s.scalars(select(TranscriptAccess).order_by(TranscriptAccess.id))]
    client.get(f"/api/calls/{ids[0]}/analysis")                 # blind, not rated: no LLM text returned -> no row
    client.get(f"/api/rating/calls/{ids[0]}")
    assert kinds() == []
    client.get(f"/api/calls/{ids[1]}/analysis")
    client.get(f"/api/calls/{ids[1]}/analysis")                 # same user + call within the window: one row
    client.get(f"/api/rating/calls/{ids[1]}")
    assert kinds() == [(ids[1], "analysis")]
    client.get(f"/api/rating/calls/{ids[2]}")                   # the rating page shows the summary too
    assert kinds() == [(ids[1], "analysis"), (ids[2], "analysis")]


def test_silence_reports_the_threshold_it_was_computed_with(env):
    from callsight.analysis import views
    from callsight.analysis.settings import SilenceRules
    assert views._long_s(SilenceRules(long_silence_s=15).version) == 15
    assert views._long_s(SilenceRules().version) == 10 and views._long_s("m") is None and views._long_s(None) is None
    client, Session, ids = env
    with Session() as s:
        s.get(CallAudioMetrics, ids[0]).metrics_version = SilenceRules(long_silence_s=12.5).version
        s.commit()
    login(client)
    assert client.get(f"/api/calls/{ids[0]}/analysis").json()["silence"]["long_s"] == 12.5


def test_queue_blind_first_and_progress(env):
    client, Session, ids = env
    freeze(Session, [ids[2], ids[0]])
    login(client)
    q = client.get("/api/rating/queue").json()
    assert [i["call_id"] for i in q["items"]][:2] == [ids[2], ids[0]] and q["items"][0]["blind"]
    assert set(q["items"][0]) == {"call_id", "start_time", "talk_seconds", "blind"}          # no agent shown
    assert q["progress"] == {"labels": 0, "labels_target": 500, "blind_frozen": True, "blind_rated": 0,
                             "blind_target": 2, "mine": False}
    client.put(f"/api/rating/calls/{ids[2]}", json={"score": 2, "note": "  "})
    client.put(f"/api/rating/calls/{ids[1]}", json={"score": 9})
    q = client.get("/api/rating/queue").json()
    assert ids[2] not in [i["call_id"] for i in q["items"]] and ids[1] not in [i["call_id"] for i in q["items"]]
    assert q["progress"]["labels"] == 2 and q["progress"]["blind_rated"] == 1 and q["progress"]["mine"]
    login(client, "admin")                                      # each rater has their own queue
    assert len(client.get("/api/rating/queue").json()["items"]) == 4


def test_label_records_prefill_and_upserts(env):
    client, Session, ids = env
    freeze(Session, [ids[0]])
    login(client)
    shown = client.get(f"/api/rating/calls/{ids[1]}").json()["estimate"]["score"]        # what the page displays
    assert shown == 4
    assert client.put(f"/api/rating/calls/{ids[1]}", json={"score": 4, "note": "عميل مرتاح", "prefill_shown": shown}
                      ).json() == {"score": 4, "note": "عميل مرتاح"}
    with Session() as s:                                        # a re-score lands AFTER the rating was given
        s.add(CallAnalysis(call_id=ids[1], model="qwen2.5:7b", prompt_version="ca1-new", status="done", summary="s",
                           topic="other", action_items=[], llm_score=9, reason="r", escalation=False))
        s.commit()
    client.put(f"/api/rating/calls/{ids[1]}", json={"score": 7, "prefill_shown": 9})     # re-rate: same row
    client.put(f"/api/rating/calls/{ids[0]}", json={"score": 3, "prefill_shown": 3})     # blind call: never a pre-fill
    client.put(f"/api/rating/calls/{ids[2]}", json={"score": 5})                         # page showed no estimate
    client.put(f"/api/rating/calls/{ids[3]}", json={"score": 5, "prefill_shown": 1})     # not a stored estimate
    with Session() as s:
        rows = {r.call_id: r for r in s.scalars(select(SatisfactionLabel))}
    assert len(rows) == 4
    assert (rows[ids[1]].score, rows[ids[1]].prefill, rows[ids[1]].changed_from_prefill, rows[ids[1]].blind,
            rows[ids[1]].note) == (7, 4, True, False, None)     # the FIRST pre-fill is kept, not the re-score's 9
    assert (rows[ids[0]].prefill, rows[ids[0]].changed_from_prefill, rows[ids[0]].blind) == (None, None, True)
    assert (rows[ids[2]].prefill, rows[ids[2]].changed_from_prefill) == (None, None)     # nothing shown = no pre-fill
    assert (rows[ids[3]].prefill, rows[ids[3]].changed_from_prefill) == (None, None)     # made-up value: ignored
    assert client.get(f"/api/rating/calls/{ids[1]}").json()["my_label"] == {"score": 7, "note": None}


@pytest.mark.parametrize("body", [{"score": 0}, {"score": 11}, {"score": "7"}, {"score": 7.5}, {},
                                  {"score": 5, "note": "x" * 501}, {"score": 5, "extra": 1},
                                  {"score": 5, "prefill_shown": 11}, {"score": 5, "prefill_shown": "4"}])
def test_bad_labels_are_refused(env, body):
    client, _, ids = env
    login(client)
    assert client.put(f"/api/rating/calls/{ids[0]}", json=body).status_code == 422


def test_unknown_or_untranscribed_calls(env):
    client, Session, ids = env
    login(client)
    assert client.get("/api/rating/calls/999999").status_code == 404
    assert client.get("/api/calls/999999/analysis").status_code == 404
    with Session() as s:
        c = make_call(s, status="running")
    assert client.put(f"/api/rating/calls/{c.id}", json={"score": 5}).status_code == 409
    d = client.get(f"/api/calls/{c.id}/analysis").json()
    assert d["analysis"] is None and d["estimate"] is None and d["flags"] == [] and d["silence"] is None


def test_analysis_prefers_current_model_and_prompt(env):
    client, Session, ids = env
    freeze(Session, [ids[3]])
    with Session() as s:
        s.add(CallAnalysis(call_id=ids[1], model="qwen3:8b", prompt_version="ca1-other", status="done",
                           summary="old", topic="other", action_items=[], llm_score=10, reason="r", escalation=False))
        s.commit()
    login(client)
    d = client.get(f"/api/calls/{ids[1]}/analysis").json()
    assert d["analysis"]["model"] == "qwen2.5:7b" and d["estimate"]["score"] == 4
    assert blindset.LABEL_TARGET == 500


def test_excluded_test_calls_never_in_queue_or_blind_pool(env, tmp_path, monkeypatch):
    client, Session, ids = env
    f = tmp_path / "rating-exclude.yaml"
    f.write_text(f"call_ids:\n  - {ids[0]}\n  - {ids[3]}\n", encoding="utf-8")
    monkeypatch.setenv("RATING_EXCLUDE_FILE", str(f))
    login(client)
    assert sorted(i["call_id"] for i in client.get("/api/rating/queue").json()["items"]) == [ids[1], ids[2]]
    with Session() as s:
        assert {cid for cid, _ in blindset.pool(s)}.isdisjoint({ids[0], ids[3]})
    f.write_text("call_ids: [abc]\n", encoding="utf-8")
    from callsight.analysis.settings import ConfigError, rating_excluded
    with pytest.raises(ConfigError):
        rating_excluded()
    monkeypatch.setenv("RATING_EXCLUDE_FILE", str(tmp_path / "missing.yaml"))
    assert rating_excluded() == frozenset()
