"""Classification rules, tested on real (anonymised) call shapes from the PBX."""
import json
from pathlib import Path

import pytest

from callsight.ingest.classify import (
    ABANDONED_IVR, ABANDONED_QUEUE, ANSWERED, BUSY, NO_ANSWER, VOICEMAIL_S,
    TeamConfig, classify, is_team_call, leg_kind,
)

FIX = json.loads((Path(__file__).parent / "fixtures" / "cdr_shapes.json").read_text(encoding="utf-8"))
TEAM = TeamConfig(ext_team={"104": "customer_service", "119": "customer_service", "116": "after_sales"},
                  queue_team={"6401": "customer_service"})


def shape(name: str, team: bool | None = None, n: int = 0) -> list[dict]:
    hits = [c["legs"] for c in FIX if c["shape"] == name and (team is None or c["team"] == team)]
    assert hits, f"no fixture for {name!r} team={team}"
    return [dict(r) for r in hits[n]]


# ---------- shapes -> status ----------

@pytest.mark.parametrize("name,expected", [
    ("Inbound: IVR:ANSWERED > EXT:ANSWERED > Q:ANSWERED", ANSWERED),
    ("Inbound: IVR:ANSWERED > Q:ANSWERED > EXT:ANSWERED", ANSWERED),
    ("Inbound: IVR:ANSWERED > EXT:ANSWERED > Q:ANSWERED > EXT:ANSWERED", ANSWERED),
    ("Inbound: IVR:ANSWERED > IVR:ANSWERED > EXT:ANSWERED > Q:ANSWERED", ANSWERED),
    ("Inbound: IVR:ANSWERED > Q:NO ANSWER", ABANDONED_QUEUE),
    ("Inbound: IVR:ANSWERED > IVR:ANSWERED > Q:NO ANSWER", ABANDONED_QUEUE),
    ("Inbound: IVR:ANSWERED", ABANDONED_IVR),
    ("Inbound: EXT:NO ANSWER > VM:VOICEMAIL", VOICEMAIL_S),
    ("Inbound: EXT:BUSY > VM:VOICEMAIL", VOICEMAIL_S),
    ("Inbound: EXT:NO ANSWER", NO_ANSWER),
    ("Inbound: EXT:ANSWERED", ANSWERED),
    ("Outbound: NUM:ANSWERED", ANSWERED),
    ("Outbound: NUM:NO ANSWER", NO_ANSWER),
    ("Outbound: NUM:BUSY", BUSY),
    ("Outbound: EXT:NO ANSWER", NO_ANSWER),
    ("Internal: EXT:ANSWERED", ANSWERED),
    ("Internal: EXT:NO ANSWER", NO_ANSWER),
    ("Internal: EXT:NO ANSWER > VM:VOICEMAIL", VOICEMAIL_S),
])
def test_every_real_shape_gets_the_right_status(name, expected):
    assert classify(shape(name), TEAM).status == expected


def test_all_fixtures_classify_without_error():
    for c in FIX:
        f = classify(c["legs"], TEAM)
        assert f.total_seconds >= 0 and f.wait_seconds >= 0 and f.talk_seconds >= 0


# ---------- IVR "ANSWERED" is never an agent answer ----------

def test_ivr_answered_is_not_answered():
    f = classify(shape("Inbound: IVR:ANSWERED"), TEAM)
    assert f.status == ABANDONED_IVR and f.agent_ext is None and f.talk_seconds == 0
    assert f.ivr_seconds > 0 and f.entry == "ivr"


def test_queue_answered_without_agent_leg_is_not_answered():
    legs = shape("Inbound: IVR:ANSWERED > EXT:ANSWERED > Q:ANSWERED", team=True)
    legs = [r for r in legs if leg_kind(r) != "extension"]   # drop the agent leg
    assert classify(legs, TEAM).status == ABANDONED_QUEUE


# ---------- timings and agent ----------

def test_queue_answered_timings_and_agent():
    legs = shape("Inbound: IVR:ANSWERED > EXT:ANSWERED > Q:ANSWERED", team=True)
    ivr, agent, queue = legs
    f = classify(legs, TEAM)
    assert f.agent_ext == agent["call_to_number"]
    assert f.team == "customer_service"
    assert f.queue_number == "6401"
    assert f.ivr_seconds == ivr["duration"]
    assert f.wait_seconds == agent["ring_duration"]
    assert f.talk_seconds == agent["talk_duration"]
    assert f.recording_file == agent["record_file"]
    assert f.customer_number == ivr["call_from_number"]
    assert f.entry == "ivr" and f.direction == "inbound" and f.hold_seconds is None


def test_abandoned_queue_wait_is_queue_duration():
    legs = shape("Inbound: IVR:ANSWERED > Q:NO ANSWER", team=True)
    f = classify(legs, TEAM)
    assert f.wait_seconds == legs[-1]["duration"]
    assert f.team == "customer_service" and f.agent_ext is None and f.missed_agent_ext is None


def test_agent_leg_without_ring_duration_uses_timestamp_gap():
    legs = shape("Inbound: IVR:ANSWERED > Q:ANSWERED > EXT:ANSWERED")
    _, queue, agent = legs
    assert agent.get("ring_duration") is None   # the PBX omits the field
    assert classify(legs, TEAM).wait_seconds == agent["timestamp"] - queue["timestamp"]


def test_transfer_credits_first_agent():
    legs = shape("Inbound: IVR:ANSWERED > EXT:ANSWERED > Q:ANSWERED > EXT:ANSWERED", team=True)
    f = classify(legs, TEAM)
    assert f.agent_ext == "119"
    assert f.talk_seconds == legs[1]["talk_duration"]
    assert f.total_seconds >= f.ivr_seconds + f.talk_seconds


def test_direct_voicemail_sets_missed_agent():
    legs = shape("Inbound: EXT:NO ANSWER > VM:VOICEMAIL")
    f = classify(legs, TEAM)
    assert f.missed_agent_ext == legs[0]["call_to_number"] and f.agent_ext is None
    assert f.entry == "direct"


def test_outbound_agent_is_caller_and_customer_is_callee():
    legs = shape("Outbound: NUM:ANSWERED", team=True)
    f = classify(legs, TEAM)
    assert f.agent_ext == legs[0]["call_from_number"]
    assert f.customer_number == legs[0]["call_to_number"]
    assert f.missed_agent_ext is None


def test_leg_order_does_not_matter():
    legs = shape("Inbound: IVR:ANSWERED > EXT:ANSWERED > Q:ANSWERED", team=True)
    assert classify(list(reversed(legs)), TEAM) == classify(legs, TEAM)


def test_late_leg_changes_classification():
    """Agent leg arrives in a later poll: call first looks abandoned, then answered."""
    legs = shape("Inbound: IVR:ANSWERED > EXT:ANSWERED > Q:ANSWERED", team=True)
    early = [r for r in legs if leg_kind(r) != "extension"]
    assert classify(early, TEAM).status == ABANDONED_QUEUE
    assert classify(legs, TEAM).status == ANSWERED


def test_missing_numbers_do_not_crash():
    legs = shape("Outbound: EXT:NO ANSWER", team=True)   # PBX row with duration None
    f = classify(legs, TEAM)
    assert f.total_seconds == 0 and f.status == NO_ANSWER


def test_rejects_mixed_or_empty_legs():
    a = shape("Inbound: IVR:ANSWERED")
    b = shape("Outbound: NUM:ANSWERED", team=True)
    with pytest.raises(ValueError):
        classify(a + b, TEAM)
    with pytest.raises(ValueError):
        classify([], TEAM)


# ---------- team filter ----------

def test_team_flag_in_fixtures_matches_filter():
    for c in FIX:
        assert is_team_call(c["legs"], TEAM) == c["team"], c["shape"]


def test_abandoned_in_team_queue_is_team_call():
    assert is_team_call(shape("Inbound: IVR:ANSWERED > Q:NO ANSWER", team=True), TEAM)


def test_ivr_only_and_other_queues_are_not_team_calls():
    assert not is_team_call(shape("Inbound: IVR:ANSWERED"), TEAM)
    assert not is_team_call(shape("Inbound: IVR:ANSWERED > Q:NO ANSWER", team=False), TEAM)


def test_dialled_feature_code_is_not_an_extension():
    legs = shape("Outbound: EXT:NO ANSWER", team=False)   # 114 dialling *51119
    assert leg_kind(legs[0]) == "external"
    assert not is_team_call(legs, TEAM)


# ---------- who hung up an outbound call (spec 2026-11-15-ring-time) ----------

@pytest.mark.parametrize("reason,disp,expected", [
    ("src hangup    ", "NO ANSWER", "agent"),
    ("dst hangup    ", "NO ANSWER", "customer"),
    ("dst extension_busy_no_forward    ", "BUSY", "busy"),
    ("src hangup    ", "ANSWERED", "agent"),
    ("dst hangup    ", "ANSWERED", "customer"),
    ("", "NO ANSWER", None),
    (None, "NO ANSWER", None),
])
def test_outbound_hangup_by(reason, disp, expected):
    legs = shape("Outbound: NUM:NO ANSWER", team=True)
    legs[0].update(reason=reason, disposition=disp)
    f = classify(legs, TEAM)
    assert f.hangup_by == expected


def test_outbound_ring_seconds_without_ring_duration_is_zero():
    legs = shape("Outbound: NUM:NO ANSWER", team=True)
    legs[0].update(reason="src hangup", ring_duration=None, duration=None)
    f = classify(legs, TEAM)
    assert f.hangup_by == "agent" and f.wait_seconds == 0


def test_outbound_ring_seconds_kept():
    legs = shape("Outbound: NUM:NO ANSWER", team=True)
    assert classify(legs, TEAM).wait_seconds == 62


def test_inbound_has_no_hangup_by():
    f = classify(shape("Inbound: EXT:ANSWERED"), TEAM)
    assert f.hangup_by is None
