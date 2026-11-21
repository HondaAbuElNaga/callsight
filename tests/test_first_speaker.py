"""First-speaker check (diarization acceptance): admin marks who speaks first on the gold calls; the system's
answer and the score appear only once every row is answered (test Postgres)."""
from datetime import date, datetime, timezone

from sqlalchemy import select

from callsight import transcripts
from callsight.api import auth
from callsight.asr import queueing
from callsight.asr.engine import SEGMENTER_VERSION
from callsight.db.models import GoldClip, RatingPick, Transcript, TranscriptTurn
from test_transcript_view import MODEL, PASSWORD, add_transcript, engine, env, login, make_call  # noqa: F401


def split(s, t, lines, unsure=False):
    """Give transcript `t` a speaker split: lines = [(speaker, start), ...]."""
    t.segmenter_version, t.diarization_version, t.speaker_unsure = SEGMENTER_VERSION, "pa31-test", unsure
    s.add_all([TranscriptTurn(transcript_id=t.id, seq=i, speaker=who, start_seconds=st, end_seconds=st + 1,
                              text="x", segment_seq=0) for i, (who, st) in enumerate(lines)])
    s.commit()


def gold(s, call):
    g = GoldClip(call_id=call.id, start_seconds=30, seconds=60, file=f"goldset/clip_{call.id}.wav")
    s.add(g)
    s.commit()
    return g


def admin(client, Session):
    with Session() as s:
        auth.create_user(s, "root", PASSWORD, "admin")
    login(client, "root")


def test_system_first_speaker_values(env):
    _, Session = env
    with Session() as s:
        none = make_call(s)
        assert transcripts.system_first_speaker(s, none.id, MODEL) == "not_split"
        c = make_call(s)
        t = add_transcript(s, c, ["نص"])
        assert transcripts.system_first_speaker(s, c.id, MODEL) == "not_split"      # done, but no split yet
        split(s, t, [("unknown", 0.1), ("customer", 0.5), ("agent", 0.2)])
        assert transcripts.system_first_speaker(s, c.id, MODEL) == "agent"          # earliest named line
        u = make_call(s)
        split(s, add_transcript(s, u, ["نص"]), [("agent", 0.1)], unsure=True)
        assert transcripts.system_first_speaker(s, u.id, MODEL) == "unsure"


def test_first_speaker_admin_only(env):
    client, Session = env
    assert client.get("/api/first-speaker").status_code == 401
    login(client)                                                     # "boss" is a supervisor here
    assert client.get("/api/first-speaker").status_code == 403
    assert client.put("/api/first-speaker/1", json={"who": "agent"}).status_code == 403


def test_answers_saved_and_score_hidden_until_all_answered(env):
    client, Session = env
    admin(client, Session)
    with Session() as s:
        calls = [make_call(s, hour=h) for h in (9, 10, 11)]
        clips = [gold(s, c) for c in calls]
        for c, lines, unsure in ((calls[0], [("agent", 0.2)], False), (calls[1], [("agent", 0.3)], False),
                                 (calls[2], [("agent", 0.1)], True)):
            split(s, add_transcript(s, c, ["نص"]), lines, unsure)
    r = client.get("/api/first-speaker").json()
    assert (r["answered"], r["total"], r["score"]) == (0, 3, None) and [x["n"] for x in r["rows"]] == [1, 2, 3]
    assert client.put(f"/api/first-speaker/{clips[0].id}", json={"who": "agent"}).json()["first_speaker"] == "agent"
    assert client.put(f"/api/first-speaker/{clips[1].id}", json={"who": "customer"}).status_code == 200
    r = client.get("/api/first-speaker").json()
    assert r["answered"] == 2 and r["score"] is None and not any("system" in x for x in r["rows"])   # not steered
    client.put(f"/api/first-speaker/{clips[2].id}", json={"who": "agent"})
    r = client.get("/api/first-speaker").json()
    assert r["score"] == {"right": 1, "wrong": 1, "unsure": 1, "not_split": 0}
    assert [x["system"] for x in r["rows"]] == ["agent", "agent", "unsure"]
    with Session() as s:
        g = s.get(GoldClip, clips[0].id)
        assert g.first_speaker_by is not None and g.first_speaker_at is not None
    client.put(f"/api/first-speaker/{clips[0].id}", json={"who": None})                 # clearing works
    with Session() as s:
        g = s.get(GoldClip, clips[0].id)
        assert (g.first_speaker, g.first_speaker_by, g.first_speaker_at) == (None, None, None)


def test_first_speaker_bad_input(env):
    client, Session = env
    admin(client, Session)
    with Session() as s:
        clip = gold(s, make_call(s))
    for body in ({"who": "boss"}, {}, {"who": "agent", "x": 1}):
        assert client.put(f"/api/first-speaker/{clip.id}", json=body).status_code == 422
    assert client.put("/api/first-speaker/99999", json={"who": "agent"}).status_code == 404


def test_resplit_takes_gold_calls_first(env, monkeypatch):
    _, Session = env
    monkeypatch.setenv("ASR_START_DATE", "2026-11-01")
    monkeypatch.setenv("ASR_MODEL", MODEL)
    cfg = queueing.AsrConfig.from_env()
    with Session() as s:
        rated, golden, newest = (make_call(s, hour=h) for h in (9, 10, 12))
        for c in (rated, golden, newest):
            add_transcript(s, c, ["نص"])
        s.add(RatingPick(call_id=rated.id, rank=1, day=date(2026, 11, 10), salt="x"))
        gold(s, golden)
        now = datetime.now(timezone.utc)
        assert queueing.resplit(s, cfg, 2, now, SEGMENTER_VERSION) == [golden.id, rated.id]
        assert queueing.resplit(s, cfg, 5, now, SEGMENTER_VERSION) == [newest.id]
        assert s.scalar(select(Transcript.redo).where(Transcript.call_id == golden.id)) is True


def test_migration_0025_down_and_up(env, engine):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    from callsight.db.models import Base
    path = Path(__file__).resolve().parent.parent / "callsight/db/migrations/versions/0025_first_speaker_check.py"
    spec = importlib.util.spec_from_file_location("m0025", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (mod.revision, mod.down_revision) == ("0025", "0024")
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mod.downgrade()
    assert "first_speaker" not in {c["name"] for c in inspect(engine).get_columns("gold_clip")}
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
    cols = {c["name"] for c in inspect(engine).get_columns("gold_clip")}
    assert cols == {c.name for c in Base.metadata.tables["gold_clip"].columns}
