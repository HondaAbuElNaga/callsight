"""Call analysis: tests for the 3-reviewer findings (2026-11-14): no model-written key names in errors, a retry that
really differs, long numbers refused, GPU priority for transcription, the real GPU lock, maintenance parking,
lock re-taken inside the model holder, training guards. DB tests run on TEST_DATABASE_URL."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from callsight.analysis import blindset, llm, tasks
from callsight.db.models import AppUser, BlindSet, CallAnalysis, SatisfactionLabel
from callsight.locks import GPU_WANTED, gpu_acquire, gpu_holder, gpu_wanted
from test_call_analysis import (CFG, NOW, PV, SECRET, TOPICS, FakeOllama, FakeRedis, GpuSpy, Seg, Session,  # noqa: F401
                                deps, engine, good, make_call, queued, row)


class Redis(FakeRedis):
    def delete(self, *keys):
        return sum(self.d.pop(k, None) is not None for k in keys)


# ---------- validation ----------

def test_schema_error_never_echoes_a_model_written_key_name():
    bad = json.dumps(good(**{f"العميل {SECRET} جواله 0551234567": 1}), ensure_ascii=False)
    with pytest.raises(llm.LlmError) as e:
        llm.validate(bad, TOPICS)
    assert str(e.value) == "answer breaks the schema (extra_field)" and e.value.fields == ("extra_field",)
    assert SECRET not in str(e.value) and "055" not in str(e.value)


@pytest.mark.parametrize("over,field", [
    ({"summary": "رقم العميل 0551234567 للتواصل."}, "summary"),
    ({"summary": "رقم العميل ٠٥٥١٢٣٤٥٦٧ للتواصل."}, "summary"),          # Arabic-Indic digits
    ({"reason": "هويته ۱۰۲۳۴۵۶۷۸۹"}, "reason"),                          # Persian digits
    ({"action_items": ["الاتصال على 055 123 4567"]}, "action_items"),     # spaces between the digits
    ({"action_items": ["تحويل إلى 1234-5678-9012"]}, "action_items"),
    ({"summary": "   "}, "summary"),                                     # whitespace only
    ({"reason": "\n\t"}, "reason"),
    ({"action_items": ["  "]}, "action_items"),
])
def test_long_numbers_and_blank_text_are_refused(over, field):
    with pytest.raises(llm.LlmError, match=field) as e:
        llm.validate(json.dumps(good(**over), ensure_ascii=False), TOPICS)
    assert "055" not in str(e.value)


def test_short_numbers_stay_allowed_and_text_is_stripped():
    o = llm.validate(json.dumps(good(summary="  اتصل 3 مرات خلال 24 ساعة بخصوص الطلب 123456.  ",
                                     reason=" راضٍ ", action_items=[" متابعة "]), ensure_ascii=False), TOPICS)
    assert o.summary.startswith("اتصل") and o.reason == "راضٍ" and o.action_items == ["متابعة"]


def test_retry_is_a_different_request_and_names_fields_only():
    client = FakeOllama([good(summary=f"{SECRET}. ب. ج. د."), good()])
    res = llm.analyze([Seg("العميل يسأل عن الفاتورة")], TOPICS, client, CFG)
    first, second = client.calls
    assert res.tries == 2 and first != second
    assert first["options"]["seed"] != second["options"]["seed"]
    assert len(second["messages"]) == len(first["messages"]) + 1
    note = second["messages"][-1]
    assert note["role"] == "user" and "summary" in note["content"] and SECRET not in note["content"]
    assert second["messages"][:2] == first["messages"]             # same prompt + transcript, plus the note


# ---------- GPU: transcription first ----------

def _asr_patches(monkeypatch, r, sent):
    from callsight import jobs
    from callsight.asr import tasks as asr_tasks
    monkeypatch.setattr(jobs, "redis_client", lambda: r)
    monkeypatch.setattr(asr_tasks, "_maint_busy", lambda: False)
    monkeypatch.setattr(asr_tasks, "_park", lambda *a: None)
    monkeypatch.setattr(asr_tasks, "holder", lambda: type("H", (), {"name": "whisper-large-v3",
                                                                    "idle_unload_s": 300, "loaded": True})())
    monkeypatch.setattr(asr_tasks, "send_transcribe", lambda cid, m, countdown=0: sent.append((cid, countdown)))
    return asr_tasks


def test_waiting_asr_task_stops_the_nlp_task_from_retaking_the_gpu(monkeypatch):
    r, sent = Redis(), []
    asr_tasks = _asr_patches(monkeypatch, r, sent)
    monkeypatch.setattr(tasks, "_redis", lambda: r)
    monkeypatch.setattr(asr_tasks, "process", lambda *a, **k: {"status": "skipped"})
    monkeypatch.setattr(asr_tasks, "_queue_analysis", lambda cid: None)
    lock = tasks._gpu_lock(60)
    with lock() as got:                                            # an Ollama answer is running
        assert got and gpu_holder(r) == "nlp"
        assert asr_tasks.transcribe_call.run(5)["gpu_wait"] and gpu_wanted(r)      # asr parks and says so
    assert gpu_holder(r) is None                                   # answer done: lock released
    with lock() as got:                                            # next nlp message: must NOT take it again
        assert got is False and gpu_holder(r) is None
    assert asr_tasks.transcribe_call.run(5) == {"status": "skipped"}               # asr gets the GPU ...
    assert gpu_holder(r) == "asr" and not gpu_wanted(r) and GPU_WANTED not in r.d   # ... and clears the flag


def test_real_gpu_lock_is_released_on_exceptions_and_fails_closed(monkeypatch):
    r = Redis()
    monkeypatch.setattr(tasks, "_redis", lambda: r)
    lock = tasks._gpu_lock(60)
    with pytest.raises(RuntimeError):
        with lock() as got:
            assert got and gpu_holder(r) == "nlp"
            raise RuntimeError("ollama blew up")
    assert gpu_holder(r) is None                                   # released although the body raised
    gpu_acquire(r, "asr", 60)
    with lock() as got:
        assert got is False
    assert gpu_holder(r) == "asr"                                  # someone else's lock is never released

    def down():
        raise ConnectionError("redis down")
    monkeypatch.setattr(tasks, "_redis", down)
    with lock() as got:
        assert got is False                                        # no Redis = no lock = no Ollama answer


def test_process_releases_the_real_lock_when_the_llm_fails(Session, monkeypatch):  # noqa: F811
    r = Redis()
    monkeypatch.setattr(tasks, "_redis", lambda: r)
    with Session() as s:
        c = make_call(s)
        queued(s, c)
        d = deps(FakeOllama([RuntimeError(SECRET)]), gpu=tasks._gpu_lock(60))
        res = tasks.process(s, c.id, "qwen2.5:7b", PV, d, now=lambda: NOW)
        assert res["status"] == "error" and gpu_holder(r) is None
        assert SECRET not in (row(s, c).error or "")


def test_model_holder_takes_the_gpu_lock_inside_its_own_lock():
    from callsight.asr import engine
    seen, allow = [], [True]

    class A:
        def load(self):
            seen.append("load")

    def before_use():
        seen.append(("claim", h.lock.locked()))
        return allow[0]
    h = engine.ModelHolder("fake", idle_unload_s=10, factory=lambda n: A(), start_timer=False, before_use=before_use)
    h.ensure_loaded()
    assert seen == [("claim", True), "load"]                       # lock claimed first, under the holder's lock
    with h.use():
        pass
    assert seen[-1] == ("claim", True)
    allow[0] = False                                               # the nlp task took the GPU in between
    with pytest.raises(engine.ModelNotReady, match="GPU"):
        h.ensure_loaded()
    with pytest.raises(engine.ModelNotReady, match="GPU"):
        with h.use():
            pytest.fail("must not use the model")
    assert h.failed_at is None and not h.busy                      # not a load failure: no retry pause


def test_asr_idle_unload_zero_is_refused(monkeypatch):
    from callsight.asr.queueing import AsrConfig
    monkeypatch.setenv("ASR_IDLE_UNLOAD_S", "0")
    with pytest.raises(RuntimeError, match="ASR_IDLE_UNLOAD_S"):
        AsrConfig.from_env()
    monkeypatch.setenv("ASR_IDLE_UNLOAD_S", "120")
    assert AsrConfig.from_env().idle_unload_s == 120


# ---------- analyze_call wrapper ----------

def _wrapper(monkeypatch, Session_, client, maint=False):
    sent = []
    monkeypatch.setattr(tasks, "deps_from_env", lambda: deps(client))
    monkeypatch.setattr(tasks, "_maint_busy", lambda: maint)
    monkeypatch.setattr(tasks, "send_analyze", lambda cid, m, pv, countdown=None: sent.append((cid, m, pv, countdown)))
    monkeypatch.setattr(tasks.wiring, "session_factory", lambda: Session_)
    return sent


def test_maintenance_wait_parks_the_row_so_the_reaper_keeps_it(Session, monkeypatch):  # noqa: F811
    from callsight.analysis import queueing
    client = FakeOllama([good()])
    sent = _wrapper(monkeypatch, Session, client, maint=True)
    with Session() as s:
        c = make_call(s)
        queued(s, c)
    before = datetime.now(timezone.utc)
    r = tasks.analyze_call.run(c.id, "qwen2.5:7b", PV)
    assert r == {"call_id": c.id, "status": "queued", "maint_wait": True} and client.calls == []
    assert sent == [(c.id, "qwen2.5:7b", PV, tasks.MAINT_WAIT_S)]
    with Session() as s:
        a = row(s, c)
        assert a.status == "queued" and a.tries == 0
        assert a.queued_at >= before + timedelta(seconds=tasks.MAINT_WAIT_S)       # due when the message is due
        # an empty nlp list no longer makes the parked row look lost (it is due in the future)
        assert queueing.reap(s, "qwen2.5:7b", a.queued_at + timedelta(minutes=1), queue_length=0) == []
        assert row(s, c).tries == 0


def test_wrapper_runs_and_resends_on_gpu_busy_and_outdated(Session, monkeypatch):  # noqa: F811
    client = FakeOllama([good()])
    sent = _wrapper(monkeypatch, Session, client)
    with Session() as s:
        c, c2, c3 = make_call(s), make_call(s), make_call(s)
        queued(s, c)
        queued(s, c2)
        queued(s, c3, pv="ca1-old")
    assert tasks.analyze_call.run(c.id)["status"] == "done" and sent == []         # defaults: current model/prompt
    monkeypatch.setattr(tasks, "deps_from_env", lambda: deps(client, gpu=GpuSpy(free=False)))
    assert tasks.analyze_call.run(c2.id, "qwen2.5:7b", PV)["gpu_busy"]
    assert sent == [(c2.id, "qwen2.5:7b", PV, tasks.GPU_WAIT_S)]
    monkeypatch.setattr(tasks, "deps_from_env", lambda: deps(client))
    assert tasks.analyze_call.run(c3.id, "qwen2.5:7b", "ca1-old")["outdated"]      # old prompt: current one queued
    assert sent[-1] == (c3.id, "qwen2.5:7b", PV, None)
    with Session() as s:
        rows = {a.prompt_version: a.status for a in s.scalars(select(CallAnalysis).where(
            CallAnalysis.call_id == c3.id))}
    assert rows == {"ca1-old": "skipped", PV: "queued"}


# ---------- blind set + training guards ----------

def test_blind_set_size_must_be_positive(Session):  # noqa: F811
    assert blindset.sample([(1, "119"), (2, "119"), (3, "104")], -1) == []
    with Session() as s:
        for _ in range(3):
            make_call(s)
        for n in (0, -1):
            with pytest.raises(ValueError, match="at least 1"):
                blindset.freeze(s, n=n, min_pool=1)
        assert not blindset.is_frozen(s)


def test_training_needs_400_calls_and_an_allowed_base_model(monkeypatch, capsys):
    from callsight.analysis import train
    assert train.MIN_TRAIN == 400 and blindset.LABEL_TARGET == 500 and blindset.DEFAULT_N == 100
    assert train.MIN_TRAIN + blindset.DEFAULT_N == blindset.LABEL_TARGET   # 500 rated = 400 training + 100 blind
    train.check_base_model("CAMeL-Lab/bert-base-arabic-camelbert-da")
    with pytest.raises(ValueError, match="research-only"):
        train.check_base_model("UBC-NLP/MARBERTv2")
    with pytest.raises(ValueError, match="allowed list"):
        train.check_base_model("some/other-bert")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    assert train.main(["--out", "unused"]) == 1 and "HF_HUB_OFFLINE=1" in capsys.readouterr().err
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(train, "BASE_MODEL", "UBC-NLP/MARBERT")
    assert train.main(["--out", "unused"]) == 1 and "research-only" in capsys.readouterr().err


def test_llm_baseline_is_one_model_and_one_prompt_only(Session):  # noqa: F811
    from callsight.analysis import train
    with Session() as s:
        a, b = make_call(s), make_call(s)
        u = AppUser(username="a", password_hash="x", role="supervisor", active=True, token_version=0, failed_logins=0)
        s.add(u)
        s.flush()
        s.add_all([BlindSet(call_id=a.id, rank=1, agent_ext="119", sampler_version="v", salt="s"),
                   BlindSet(call_id=b.id, rank=2, agent_ext="119", sampler_version="v", salt="s"),
                   SatisfactionLabel(call_id=a.id, user_id=u.id, score=4, blind=True),
                   SatisfactionLabel(call_id=b.id, user_id=u.id, score=6, blind=True)])
        common = dict(status="done", summary="s", topic="other", action_items=[], reason="r", escalation=False)
        s.add_all([CallAnalysis(call_id=a.id, model="qwen2.5:7b", prompt_version=PV, llm_score=5, **common),
                   CallAnalysis(call_id=a.id, model="qwen3:8b", prompt_version=PV, llm_score=9, finished_at=NOW,
                                **common),                                # newer row of ANOTHER model: ignored
                   CallAnalysis(call_id=b.id, model="qwen2.5:7b", prompt_version="ca1-old", llm_score=10, **common)])
        s.commit()
        _, test = train.load_examples(s, "whisper-large-v3", "qwen2.5:7b", PV)
        assert {e.call_id: e.llm_score for e in test} == {a.id: 5, b.id: None}     # b: old prompt only -> no baseline
        _, test = train.load_examples(s, "whisper-large-v3")
        assert [e.llm_score for e in test] == [None, None]
