"""Diarization (spec 2026-11-17-diarization) against the test Postgres: the worker saves words / turns / lines /
metrics, the page + .txt show agent and customer lines, agents never get the talk metrics, KPI averages,
`transcribe --resplit` and migration 0024 up / down. Skipped without TEST_DATABASE_URL."""
import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select

from callsight import kpi
from callsight.asr import diarize as dz
from callsight.asr import queueing, tasks
from callsight.asr.engine import SEGMENTER_VERSION, Result, Segment
from callsight.db.models import Base, RatingPick, SpeakerTurn, Transcript, TranscriptTurn
from test_transcript_view import MODEL, add_transcript, engine, env, login, make_call  # noqa: F401

A, B = "SPEAKER_00", "SPEAKER_01"
TURNS = [dz.Turn(A, 0.0, 1.5), dz.Turn(B, 2.5, 4.0)]


def result():
    return Result(segments=[
        Segment(0, 0.0, 29.5, "السلام عليكم معك ابي الفاتورة",
                words=[("السلام", 0.2, 0.6), ("عليكم", 0.6, 1.0), ("معك", 1.0, 1.3), ("ابي", 2.6, 2.9),
                       ("الفاتورة", 2.9, 3.5)]),
        Segment(1, 29.5, 59.0, "", "silence"),
        Segment(2, 59.0, 88.0, "نص بلا توقيت")],                 # kept text, but the word times failed
        text="السلام عليكم معك ابي الفاتورة نص بلا توقيت", audio_seconds=88)


def saved(Session, direction="inbound", res=None):
    with Session() as s:
        c = make_call(s, direction=direction)
        row = Transcript(call_id=c.id, model=MODEL, status="running")
        s.add(row)
        s.commit()
        res = res or result()
        tasks._save(s, row, res, None, 1.0, datetime.now(timezone.utc))
        tasks.save_speakers(s, row, res, TURNS, direction, "pa31-test")
        return c.id, row.id


def test_worker_saves_words_turns_lines_and_metrics(env):
    _, Session = env
    _, tid = saved(Session)
    with Session() as s:
        t = s.get(Transcript, tid)
        assert (t.diarization_version, t.speaker_unsure, t.speaker_reason) == ("pa31-test", False, "first_greets")
        assert t.talk_ratio == pytest.approx(0.5) and t.interruptions_agent == 0
        assert [x.speaker_raw for x in s.scalars(select(SpeakerTurn).order_by(SpeakerTurn.seq))] == [A, B]
        lines = s.scalars(select(TranscriptTurn).order_by(TranscriptTurn.seq)).all()
        assert [(x.speaker, x.text) for x in lines] == [("agent", "السلام عليكم معك"), ("customer", "ابي الفاتورة")]
        assert t.segments[0].words[0] == ["السلام", 0.2, 0.6] and t.segments[2].words is None
        assert t.search_text                                         # search still built from the windows


def test_resave_replaces_the_old_split(env):
    _, Session = env
    _, tid = saved(Session)
    with Session() as s:
        row = s.get(Transcript, tid)
        tasks.save_speakers(s, row, result(), TURNS, "inbound", "pa31-test")
        assert s.scalar(select(func.count()).select_from(TranscriptTurn)) == 2
        assert s.scalar(select(func.count()).select_from(SpeakerTurn)) == 2


def test_page_shows_speakers_and_agents_never_get_talk_metrics(env):
    client, Session = env
    cid, _ = saved(Session)
    login(client)
    d = client.get(f"/api/calls/{cid}/transcript").json()
    assert d["split"] is True and d["speaker_unsure"] is False
    assert [(x["speaker"], x["dropped"], x["text"]) for x in d["segments"]] == [
        ("agent", False, "السلام عليكم معك"), ("customer", False, "ابي الفاتورة"), (None, True, ""),
        (None, False, "نص بلا توقيت")]
    assert d["talk"]["ratio"] == pytest.approx(0.5)
    client.post("/api/logout")
    login(client, "agent1")                                      # the agent of call ext 119
    d = client.get(f"/api/calls/{cid}/transcript").json()
    assert d["split"] is True and "talk" not in d


def test_txt_export_names_the_speakers(env):
    client, Session = env
    cid, _ = saved(Session)
    login(client)
    body = client.get(f"/api/calls/{cid}/transcript.txt").content.decode("utf-8-sig")
    assert "] الموظف: السلام عليكم معك" in body and "] العميل: ابي الفاتورة" in body
    assert "] نص بلا توقيت" in body


def test_unsure_split_shows_speaker_1_and_2_without_metrics(env):
    client, Session = env
    res = Result(segments=[Segment(0, 0, 29.5, "الو نعم", words=[("الو", 0.2, 0.5), ("نعم", 2.6, 2.9)])],
                 text="الو نعم", audio_seconds=29.5)
    cid, _ = saved(Session, res=res)                             # nobody greets: unsure
    login(client)
    d = client.get(f"/api/calls/{cid}/transcript").json()
    assert d["speaker_unsure"] is True and "talk" not in d
    assert [x["speaker"] for x in d["segments"]] == ["speaker_1", "speaker_2"]


def test_without_split_the_view_is_unchanged(env):
    client, Session = env
    with Session() as s:
        c = make_call(s)
        add_transcript(s, c, ["الفاتورة", "سر"], flags=[None, "blocklist"])
    login(client)
    d = client.get(f"/api/calls/{c.id}/transcript").json()
    assert d["split"] is False and [x["speaker"] for x in d["segments"]] == [None, None]


def test_kpi_talk_averages_only_named_splits(env):
    from callsight.ingest.classify import TeamConfig
    _, Session = env
    saved(Session)
    with Session() as s:                                         # an unsplit call does not count
        add_transcript(s, make_call(s), ["نص"])
        teams = TeamConfig(ext_team={"119": "customer_service", "104": "customer_service"})
        out = kpi.compute(s, date(2026, 11, 10), date(2026, 11, 11), teams=teams)
    assert out["summary"]["talk"]["calls"] == 1 and out["summary"]["talk"]["ratio"] == pytest.approx(0.5)
    assert next(a for a in out["agents"] if a["ext"] == "119")["talk"]["calls"] == 1


def test_resplit_queues_rating_set_first_then_newest(env, monkeypatch):
    _, Session = env
    monkeypatch.setenv("ASR_START_DATE", "2026-11-01")
    monkeypatch.setenv("ASR_MODEL", MODEL)
    cfg = queueing.AsrConfig.from_env()
    with Session() as s:
        old = [make_call(s, hour=h) for h in (9, 10, 11)]
        for c in old:
            add_transcript(s, c, ["نص"])
        new = make_call(s, hour=12)
        add_transcript(s, new, ["نص"]).segmenter_version = SEGMENTER_VERSION     # already has word times
        s.add(RatingPick(call_id=old[0].id, rank=1, day=date(2026, 11, 10), salt="x"))
        s.commit()
        now = datetime.now(timezone.utc)
        assert queueing.resplit(s, cfg, 2, now, SEGMENTER_VERSION) == [old[0].id, old[2].id]
        assert s.scalar(select(Transcript.redo).where(Transcript.call_id == old[0].id)) is True
        assert queueing.resplit(s, cfg, 5, now, SEGMENTER_VERSION) == [old[1].id]   # the 2 already queued are not "done"
        with pytest.raises(ValueError):
            queueing.resplit(s, cfg, 501, now, SEGMENTER_VERSION)


def test_migration_0024_down_and_up(env, engine):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parent.parent / "callsight/db/migrations/versions/0024_diarization.py"
    spec = importlib.util.spec_from_file_location("m0024", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (mod.revision, mod.down_revision) == ("0024", "0023")
    try:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                mod.downgrade()
        insp = inspect(engine)
        assert not {"speaker_turn", "transcript_turn"} & set(insp.get_table_names())
        assert "talk_ratio" not in {c["name"] for c in insp.get_columns("transcript")}
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                mod.upgrade()
        insp = inspect(engine)
        for t in ("transcript", "transcript_segment", "speaker_turn", "transcript_turn"):
            assert {c["name"] for c in insp.get_columns(t)} == {c.name for c in Base.metadata.tables[t].columns}, t
    finally:
        Base.metadata.create_all(engine)
