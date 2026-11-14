"""Transcript view, .txt export and word search (spec 2026-11-14-transcript-view), against the test Postgres.

Runs on TEST_DATABASE_URL (a *_test database); skipped when it isn't reachable.
"""
import importlib.util
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from callsight import transcripts
from callsight.api import auth
from callsight.api.main import ApiConfig, create_app
from callsight.config import RIYADH
from callsight.db.models import Agent, AppUser, Base, Call, Transcript, TranscriptAccess, TranscriptSegment

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
URL = os.getenv("TEST_DATABASE_URL")
SECRET = "y" * 40
PASSWORD = "correct-horse-battery"
MODEL = "whisper-large-v3"
DAY = date(2026, 11, 10)
DAYQ = {"from": DAY.isoformat(), "to": DAY.isoformat()}
NUMBER = "0551234567"


@pytest.fixture(scope="module")
def engine():
    if not URL or "_test" not in URL:
        pytest.skip("TEST_DATABASE_URL not set (must point to a *_test database)")
    eng = create_engine(URL, connect_args={"connect_timeout": 3})
    try:
        with eng.connect():
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database not reachable: {exc}")
    Base.metadata.drop_all(eng)
    with eng.begin() as c:   # the fixture creates the extension before the index (create_all also does)
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


_n = [0]


def make_call(s, hour=10, agent="119", direction="inbound", number=NUMBER, day=DAY) -> Call:
    _n[0] += 1
    start = datetime(day.year, day.month, day.day, hour, 5, 7, tzinfo=RIYADH)
    c = Call(uid=f"tv{_n[0]}", direction=direction, entry="queue", start_time=start, customer_number=number,
             end_time=start + timedelta(seconds=70), talk_seconds=60, total_seconds=70, status="answered",
             agent_ext=agent, recording_status="none")
    s.add(c)
    s.commit()
    return c


def add_transcript(s, call, texts, model=MODEL, status="done", finished=None, flags=None) -> Transcript:
    """texts: one string per 29 s window; flags: optional flag per window (dropped windows keep their text here
    only to prove it never reaches the view, the export or the search)."""
    flags = flags or [None] * len(texts)
    t = Transcript(call_id=call.id, model=model, status=status, text=" ".join(texts),
                   finished_at=finished or datetime.now(timezone.utc) if status == "done" else None,
                   dropped_windows=sum(1 for f in flags if f))
    s.add(t)
    s.flush()
    segs = [TranscriptSegment(transcript_id=t.id, seq=i, start_seconds=i * 29.5, end_seconds=(i + 1) * 29.5,
                              text=x, flag=f) for i, (x, f) in enumerate(zip(texts, flags))]
    s.add_all(segs)
    if status == "done":
        t.search_text = transcripts.search_text(segs)
    s.commit()
    return t


@pytest.fixture
def env(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE transcript_access, transcript_segment, transcript, recording_play, app_user, "
                       "call_leg, call, agent RESTART IDENTITY CASCADE"))
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session() as s:
        s.add_all([Agent(ext="119", name="Agent 119", team="customer_service", in_team=True),
                   Agent(ext="104", name="Agent 104", team="customer_service", in_team=True)])
        s.commit()
        auth.create_user(s, "boss", PASSWORD, "supervisor", "114")
        s.add(AppUser(username="agent1", password_hash=auth.hash_password(PASSWORD), role="agent", ext="119",
                      active=True, token_version=0, failed_logins=0))   # Phase 6 role: must be denied for now
        s.commit()
    client = TestClient(create_app(Session, ApiConfig(secret=SECRET, audio_dir=Path("."), asr_model=MODEL)))
    return client, Session


def login(client, user="boss"):
    assert client.post("/api/login", json={"username": user, "password": PASSWORD}).status_code == 200


def accesses(Session, kind=None):
    with Session() as s:
        q = select(TranscriptAccess)
        if kind:
            q = q.where(TranscriptAccess.kind == kind)
        return s.scalars(q).all()


# ---------- normalisation + word rules ----------

def test_search_normalize_folds_alif_ta_marbuta_ya_harakat_digits():
    assert transcripts.search_normalize("الفَاتورة إلى مستشفى ٣٤") == "الفاتوره الي مستشفي 34"
    assert transcripts.search_normalize("مسؤول") == transcripts.search_normalize("مسوول")


def test_parse_words_rules():
    assert transcripts.parse_words("  ") == ()
    assert transcripts.parse_words("الفاتورة  مسؤول") == ("الفاتوره", "مسوول")
    for bad in ("ا", "كلمة و", "a b c d e f", "x" * 101, "50%", "a_b", "ab\\cd", "رقم؛ الهوية"):
        with pytest.raises(transcripts.BadSearch) as exc:
            transcripts.parse_words(bad)
        assert bad not in str(exc.value)


# ---------- auth ----------

def test_transcript_routes_need_login_and_manager_role(env):
    client, Session = env
    with Session() as s:
        c = make_call(s)
        add_transcript(s, c, ["مرحبا"])
    for url in (f"/api/calls/{c.id}/transcript", f"/api/calls/{c.id}/transcript.txt"):
        assert client.get(url).status_code == 401, url
    login(client, "agent1")                      # future agent role: denied until Phase 6
    for url in (f"/api/calls/{c.id}/transcript", f"/api/calls/{c.id}/transcript.txt"):
        assert client.get(url).status_code == 403, url
    assert client.get("/api/calls", params={**DAYQ, "words": "مرحبا"}).status_code == 403
    assert client.get("/api/calls/summary", params={**DAYQ, "words": "مرحبا"}).status_code == 403
    assert accesses(Session) == []


# ---------- view ----------

def test_404_without_transcript_and_status_without_audit(env):
    client, Session = env
    login(client)
    with Session() as s:
        none, queued = make_call(s), make_call(s, hour=11)
        add_transcript(s, queued, [], status="queued")
    assert client.get(f"/api/calls/{none.id}/transcript").status_code == 404
    assert client.get(f"/api/calls/{none.id}/transcript.txt").status_code == 404
    r = client.get(f"/api/calls/{queued.id}/transcript")
    assert r.status_code == 200 and r.json()["status"] == "queued" and r.json()["segments"] == []
    assert client.get(f"/api/calls/{queued.id}/transcript.txt").status_code == 404
    assert accesses(Session) == []               # status polling writes nothing


def test_view_returns_segments_hides_dropped_text_and_audits_once(env):
    client, Session = env
    login(client)
    with Session() as s:
        c = make_call(s)
        add_transcript(s, c, ["السلام عليكم", "نص هلوسة سري", "الفاتورة"], flags=[None, "blocklist", None])
    r = client.get(f"/api/calls/{c.id}/transcript")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "done" and d["model"] == MODEL and d["created_at"]
    assert [x["dropped"] for x in d["segments"]] == [False, True, False]
    assert d["segments"][0] == {"seq": 0, "start": 0.0, "end": 29.5, "speaker": None, "dropped": False,
                                "text": "السلام عليكم"}
    assert "هلوسة" not in r.text
    assert r.headers["cache-control"] == "no-store"
    client.get(f"/api/calls/{c.id}/transcript")                     # within AUDIT_WINDOW: same view
    rows = accesses(Session, "view")
    assert len(rows) == 1 and rows[0].call_id == c.id
    with Session() as s:                                            # after the window: a new view row
        s.query(TranscriptAccess).update({"accessed_at": datetime.now(timezone.utc) - timedelta(minutes=11)})
        s.commit()
    client.get(f"/api/calls/{c.id}/transcript")
    assert len(accesses(Session, "view")) == 2


def test_displayed_transcript_choice(env):
    client, Session = env
    login(client)
    now = datetime.now(timezone.utc)
    with Session() as s:
        a, b, c = make_call(s), make_call(s, hour=11), make_call(s, hour=12)
        add_transcript(s, a, ["نموذج حالي"], finished=now - timedelta(days=2))
        add_transcript(s, a, ["نموذج اخر"], model="audar-asr-turbo", finished=now)
        add_transcript(s, b, ["الاقدم"], model="m-old", finished=now - timedelta(days=2))
        add_transcript(s, b, ["الاحدث"], model="m-new", finished=now)
        add_transcript(s, b, [], status="queued")                     # ASR_MODEL row not done yet
        add_transcript(s, c, [], model="m-old", status="error")
        add_transcript(s, c, [], status="running")
    assert client.get(f"/api/calls/{a.id}/transcript").json()["model"] == MODEL
    assert client.get(f"/api/calls/{b.id}/transcript").json()["model"] == "m-new"
    assert client.get(f"/api/calls/{c.id}/transcript").json()["status"] == "running"


# ---------- .txt export ----------

def test_txt_export_format_and_audit(env):
    client, Session = env
    login(client)
    with Session() as s:
        c = make_call(s, number="+966551234567")
        add_transcript(s, c, ["السلام عليكم", "نص هلوسة سري", "رقم الفاتورة   ٤٥"] + ["تمام"] * 2,
                       flags=[None, "repeat", None, None, None])
    r = client.get(f"/api/calls/{c.id}/transcript.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain") and "utf-8" in r.headers["content-type"]
    assert r.headers["cache-control"] == "private, no-store"
    assert r.headers["content-disposition"] == f'attachment; filename="callsight-call-{c.id}-20261110.txt"'
    raw = r.content
    assert raw.startswith(b"\xef\xbb\xbf") and raw.endswith(b"\r\n")
    body = raw.decode("utf-8")[1:]
    assert "\n" not in body.replace("\r\n", "")                        # CRLF only
    lines = body.split("\r\n")[:-1]
    assert all(line.startswith("‏") for line in lines)            # RLM keeps [mm:ss] in place in Notepad
    plain = [line[1:] for line in lines]
    assert "2026-11-10 10:05:07" in plain[1]
    assert plain[2] == "الموظف: 119 · Agent 119" and plain[3] == "النوع: وارد"
    assert plain[4] == "رقم العميل: ****4567"
    assert "551234567" not in body and "55123" not in body
    assert plain[5] == f"النموذج: {MODEL}"
    assert plain[6] == "تفريغ آلي — قد يحتوي أخطاء؛ الأرقام المنطوقة في المكالمة غير مخفية"
    assert plain[8:] == ["[00:00] السلام عليكم", "[00:29] — (صمت/غير واضح)", "[00:59] رقم الفاتورة ٤٥",
                         "[01:28] تمام", "[01:58] تمام"]
    assert "هلوسة" not in body
    client.get(f"/api/calls/{c.id}/transcript.txt")
    assert len(accesses(Session, "export")) == 2                       # every download audited
    assert accesses(Session, "view") == []


def test_mask_and_mmss():
    assert transcripts.mask_number(None) == "–" and transcripts.mask_number("1234") == "1234"
    assert transcripts.mask_number("00966 55 123 4567") == "****4567"
    assert transcripts.mmss(3725.9) == "62:05"


# ---------- search ----------

def search(client, words, **extra):
    params = {**DAYQ, "words": words, **extra}
    lst = client.get("/api/calls", params=params)
    summ = client.get("/api/calls/summary", params=params)
    assert lst.status_code == 200 and summ.status_code == 200, (lst.status_code, summ.status_code)
    ids = sorted(i["id"] for i in lst.json()["items"])
    assert lst.json()["total"] == summ.json()["total"] == len(ids)     # summary = list
    assert all(i["transcript_match"] for i in lst.json()["items"])
    return ids


def test_search_normalised_and_and_filters(env):
    client, Session = env
    login(client)
    with Session() as s:
        c1 = make_call(s, hour=9)
        add_transcript(s, c1, ["أبغى أكلم المسوول عن الفاتوره", "شكرا"])
        c2 = make_call(s, hour=10, agent="104")
        add_transcript(s, c2, ["الفاتورة وصلت متأخرة إلى المستشفى"])
        c3 = make_call(s, hour=11)
        add_transcript(s, c3, ["مسؤول الحسابات"])
        c4 = make_call(s, hour=12)                                     # no transcript at all
        c5 = make_call(s, day=DAY + timedelta(days=1))                  # other day
        add_transcript(s, c5, ["الفاتورة"])
        c6 = make_call(s, hour=13)                                      # dropped window text not searchable
        add_transcript(s, c6, ["الفاتورة سرية"], flags=["blocklist"])
        c7 = make_call(s, hour=14)                                      # only a queued transcript
        add_transcript(s, c7, [], status="queued")
    assert search(client, "الفاتورة") == [c1.id, c2.id]                 # ة/ه folded both ways
    assert search(client, "مسؤول") == [c1.id, c3.id]                    # ؤ→و finds "مسوول"
    assert search(client, "المسؤول الفاتوره") == [c1.id]                 # all words (AND)
    assert search(client, "مستشفى") == [c2.id]                          # ى→ي
    assert search(client, "اكلم") == [c1.id]                            # أ→ا
    assert search(client, "الفاتورة", agent="104") == [c2.id]           # combined with the agent filter
    assert search(client, "الفاتورة", direction="outbound") == []
    nxt = (DAY + timedelta(days=1)).isoformat()
    assert search(client, "الفاتورة", to=nxt) == [c1.id, c2.id, c5.id]  # combined with the date range
    assert search(client, "سرية") == []
    assert c4.id and c7.id
    r = client.get("/api/calls", params=DAYQ)                            # no words: no filter, no badge
    assert r.json()["total"] == 6 and not any(i["transcript_match"] for i in r.json()["items"])


def test_search_counts_a_call_once_and_uses_displayed_transcript(env):
    client, Session = env
    login(client)
    with Session() as s:
        c = make_call(s)
        add_transcript(s, c, ["الفاتورة"])
        add_transcript(s, c, ["الفاتورة الشهرية"], model="audar-asr-turbo")
    assert search(client, "الفاتورة") == [c.id]                         # EXISTS: never twice
    assert search(client, "الشهرية") == []                               # only the displayed (ASR_MODEL) one


def test_search_escapes_like_wildcards(env, engine):
    from callsight.api.calls import CallFilter, list_calls, summary
    client, Session = env
    with Session() as s:
        c = make_call(s)
        add_transcript(s, c, ["الفاتورة"])
        for w in ("%%", "__", "\\%"):
            f = CallFilter(DAY, DAY, words=(w,), asr_model=MODEL)
            assert list_calls(s, f)["total"] == summary(s, f)["total"] == 0, w


def test_search_422_never_echoes_words_and_nothing_logged(env, caplog):
    client, Session = env
    login(client)
    with Session() as s:
        c = make_call(s)
        add_transcript(s, c, ["كلمةسرية"])
    caplog.set_level(logging.DEBUG)
    for bad in ("ب", "كلمةسرية ب", "كلمةسرية%", "كلمةسرية " * 6, "كلمةسرية" * 13):
        r = client.get("/api/calls", params={**DAYQ, "words": bad})
        assert r.status_code == 422 and "كلمةسرية" not in r.text, bad
    assert search(client, "كلمةسرية") == [c.id]
    assert "كلمةسرية" not in caplog.text and "كلمهسريه" not in caplog.text


# ---------- pg_trgm ----------

def test_pg_trgm_handles_arabic_and_index_is_used(env, engine):
    with engine.connect() as conn:
        # Arabic letters count as word characters: one 8-letter word gives 9 trigrams (multibyte ones are shown
        # hashed); if they were treated as separators there would be none and the index could not help.
        assert len(conn.execute(text("SELECT show_trgm('الفاتوره')")).scalar()) == 9
        assert conn.execute(text("SELECT show_trgm('؟ ، ؛')")).scalar() == []
        assert conn.execute(text("SELECT similarity('الفاتوره', 'الفاتوره الشهريه')")).scalar() > 0.3
        assert conn.execute(text("SELECT similarity('الفاتوره', 'مستشفي')")).scalar() == 0
        conn.execute(text("SET enable_seqscan = off"))
        plan = "\n".join(r[0] for r in conn.execute(text(
            "EXPLAIN SELECT id FROM transcript WHERE search_text LIKE '%فاتوره%'")))
        assert "ix_transcript_search_trgm" in plan



# ---------- worker writes search_text ----------

def test_worker_save_writes_search_text_without_dropped(env):
    from callsight.asr import tasks
    from callsight.asr.engine import Result, Segment
    _, Session = env
    with Session() as s:
        c = make_call(s)
        row = Transcript(call_id=c.id, model=MODEL, status="running")
        s.add(row)
        s.commit()
        res = Result(segments=[Segment(0, 0, 29.5, "الفاتورة إلى"), Segment(1, 29.5, 59, "", "silence")],
                     text="الفاتورة إلى", audio_seconds=59)
        tasks._save(s, row, res, None, 1.0, datetime.now(timezone.utc))
        s.refresh(row)
        assert row.search_text == "الفاتوره الي"


# ---------- reindex CLI ----------

def test_cli_transcripts_reindex(env, engine, monkeypatch, capsys):
    from callsight import app as wiring, cli
    _, Session = env
    with Session() as s:
        c = make_call(s)
        t = add_transcript(s, c, ["الفاتورة", "سر"], flags=[None, "repeat"])
        t.search_text = "old"
        s.commit()
    monkeypatch.setattr(wiring, "session_factory", lambda: sessionmaker(engine, expire_on_commit=False))
    assert cli.main(["transcripts", "reindex"]) == 0
    assert "1 transcript(s) reindexed" in capsys.readouterr().out
    with Session() as s:
        assert s.get(Transcript, t.id).search_text == "الفاتوره"


def test_reindex_skips_rows_no_longer_done(env):
    """reindex locks + re-checks each row: a transcript put back to queued (--force redo) is left alone."""
    _, Session = env
    with Session() as s:
        c = make_call(s)
        t = add_transcript(s, c, ["الفاتورة"])
        t.search_text = "old"
        s.commit()
        real_scalars = s.scalars
        state = {"flipped": False}

        def scalars(stmt, *a, **k):          # flip the row to queued right after the id batch was read
            r = real_scalars(stmt, *a, **k)
            if not state["flipped"]:
                state["flipped"] = True
                ids = list(r)
                s.execute(text("UPDATE transcript SET status = 'queued' WHERE id = :i"), {"i": t.id})
                return iter(ids)
            return r
        s.scalars = scalars
        assert transcripts.reindex(s) == 0          # the row is skipped, not rewritten
        s.scalars = real_scalars
        assert s.get(Transcript, t.id).search_text == "old"


# ---------- migration 0007 ----------

def load_migration():
    path = Path(__file__).resolve().parent.parent / "callsight/db/migrations/versions/0007_transcript_view.py"
    spec = importlib.util.spec_from_file_location("m0007", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_0007_backfill_up_and_down(env, engine):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    mod = load_migration()
    assert mod.revision == "0007" and mod.down_revision in ("0005", "0006")
    _, Session = env
    with Session() as s:
        calls = [make_call(s, hour=h) for h in range(3)]
        add_transcript(s, calls[0], ["الفاتورة", "سر مخفي"], flags=[None, "blocklist"])
        add_transcript(s, calls[1], ["مسؤول إلى"])
        add_transcript(s, calls[2], [], status="queued")
    try:
        with engine.begin() as conn:                                    # back to the 0005 schema
            conn.execute(text("DROP TABLE transcript_access"))
            conn.execute(text("DROP INDEX ix_transcript_search_trgm"))
            conn.execute(text("ALTER TABLE transcript DROP COLUMN search_text"))
        mod.BATCH = 1                                                   # exercise the batching
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                mod.upgrade()
        insp = inspect(engine)
        assert {c["name"] for c in insp.get_columns("transcript")} == \
            {c.name for c in Base.metadata.tables["transcript"].columns}
        assert {c["name"] for c in insp.get_columns("transcript_access")} == \
            {c.name for c in Base.metadata.tables["transcript_access"].columns}
        assert "ix_transcript_search_trgm" in {i["name"] for i in insp.get_indexes("transcript")}
        with engine.connect() as conn:
            got = dict(conn.execute(text("SELECT call_id, search_text FROM transcript")).all())
        assert got == {calls[0].id: "الفاتوره", calls[1].id: "مسوول الي", calls[2].id: None}
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                mod.downgrade()
        insp = inspect(engine)
        assert "transcript_access" not in insp.get_table_names()
        assert "search_text" not in {c["name"] for c in insp.get_columns("transcript")}
        assert {"transcript", "transcript_segment", "call"} <= set(insp.get_table_names())   # additive only
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS transcript_access, transcript_segment, transcript CASCADE"))
        Base.metadata.create_all(engine)
        with engine.connect() as conn:
            assert conn.execute(select(func.count()).select_from(Transcript)).scalar() == 0
