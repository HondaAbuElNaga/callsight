"""Diarization (spec 2026-11-17-diarization): word times, words -> speaker lines, agent naming, talk metrics.
CPU only: fake tokenizer, fake adapter and hand-made pyannote turns (no torch, no GPU)."""
import pytest

from callsight.asr import diarize as dz
from callsight.asr import engine
from callsight.asr.models import token_words

A, B = "SPEAKER_00", "SPEAKER_01"


def W(text, s, e, seq=0):
    return dz.Word(text, s, e, seq)


def T(sp, s, e):
    return dz.Turn(sp, s, e)


# ---------- Whisper tokens -> words ----------

class FakeTok:
    """Each id is a piece of text; 1 and 2 are special (start / end)."""
    all_special_ids = [1, 2]

    def __init__(self, pieces):
        self.pieces = pieces

    def decode(self, ids, skip_special_tokens=True):
        return "".join(self.pieces[i] for i in ids if i not in self.all_special_ids)


def test_token_words_groups_sub_tokens_into_the_text_words():
    tok = FakeTok({3: " ال", 4: "سلام", 5: " عليكم", 6: " معك", 7: " أحمد"})
    seq, times = [1, 3, 4, 5, 6, 7, 2], [0, 0.10, 0.30, 0.62, 1.10, 1.40, 1.90]
    words = token_words(tok, seq, times, "السلام عليكم معك أحمد", 2.0)
    assert [w for w, _, _ in words] == ["السلام", "عليكم", "معك", "أحمد"]
    assert words[0][1:] == (0.1, 0.62) and words[1][1:] == (0.62, 1.1) and words[3][1:] == (1.4, 1.9)


def test_token_words_gives_nothing_when_the_decode_does_not_match():
    tok = FakeTok({3: " نعم"})
    assert token_words(tok, [1, 3, 2], [0, 0.1, 0.5], "لا", 1.0) == []         # text differs: no times at all
    assert token_words(tok, [1, 2], [0, 0.1], "", 1.0) == []


def test_window_words_shift_and_count_the_overlap_once():
    # window 2 starts at sample 29 s; display range 29.5..59.0: a word at 29.1-29.4 belongs to window 1
    got = engine.window_words([("قبل", 0.1, 0.4), ("بعد", 0.6, 1.0)], 29.0, 29.5, 59.0)
    assert got == [("بعد", 29.6, 30.0)]


class FakeAdapter:
    window_s = 30.0

    def __init__(self, with_words=True):
        self.with_words = with_words

    def transcribe(self, audio):
        return "السلام عليكم"

    def transcribe_words(self, audio):
        return "السلام عليكم", [("السلام", 0.5, 0.9), ("عليكم", 0.9, 1.3)]


def test_engine_keeps_word_times_and_the_same_text():
    audio = [3000, -3000] * 8000 * 5                # 10 s of loud audio at 16 kHz (no silence window)
    with_w = engine.transcribe_audio(FakeAdapter(), audio)
    plain = engine.transcribe_audio(type("P", (), {"window_s": 30.0, "transcribe": lambda self, a: "السلام عليكم"})(),
                                    audio)
    assert with_w.text == plain.text == "السلام عليكم"
    assert with_w.segments[0].words == [("السلام", 0.5, 0.9), ("عليكم", 0.9, 1.3)]
    assert plain.segments[0].words == []
    assert engine.SEGMENTER_VERSION.endswith("-wts")


# ---------- words -> lines ----------

def test_assign_splits_words_at_the_speaker_change():
    words = [W("السلام", 0.2, 0.6), W("عليكم", 0.6, 1.0), W("وعليكم", 1.3, 1.8), W("السلام", 1.8, 2.2),
             W("ابي", 2.6, 2.9)]
    turns = [T(A, 0.0, 1.1), T(B, 1.2, 3.0)]
    lines = dz.assign(words, turns)
    assert [(ln.speaker, ln.text) for ln in lines] == [(A, "السلام عليكم"), (B, "وعليكم السلام ابي")]


def test_word_between_turns_goes_to_the_nearest_turn_and_no_turns_is_unknown():
    turns = [T(A, 0.0, 1.0), T(B, 5.0, 6.0)]
    assert dz.assign([W("نعم", 1.2, 1.4)], turns)[0].speaker == A
    assert dz.assign([W("نعم", 4.6, 4.8)], turns)[0].speaker == B
    assert dz.assign([W("نعم", 1.0, 1.4)], [])[0].speaker == dz.UNKNOWN


# ---------- who is the agent ----------

def _lines(*pairs):
    return [dz.Line(sp, s, s + 1, text.split()) for sp, s, text in pairs]


TURNS = [T(A, 0.0, 2.0), T(B, 2.5, 5.0), T(A, 5.5, 8.0)]


def test_inbound_first_speaker_who_greets_is_the_agent():
    n = dz.name_speakers(_lines((A, 0.0, "المعهد السعودي معك سارة"), (B, 2.5, "ابي اسأل")), TURNS, "inbound")
    assert (n.agent, n.reason) == (A, "first_greets")


def test_inbound_other_speaker_greets_when_the_first_does_not():
    n = dz.name_speakers(_lines((A, 0.0, "الو"), (B, 2.5, "السلام عليكم حياك")), TURNS, "inbound")
    assert (n.agent, n.reason) == (B, "other_greets")


def test_unsure_without_a_greeting_or_with_one_speaker():
    assert dz.name_speakers(_lines((A, 0.0, "الو"), (B, 2.5, "نعم")), TURNS, "inbound").unsure
    assert dz.name_speakers(_lines((A, 0.0, "السلام عليكم")), [T(A, 0, 3)], "inbound").reason == "one_speaker"
    late = _lines((A, 0.0, "الو"), (B, 12.0, "السلام عليكم"))                      # greeting after 10 s
    assert dz.name_speakers(late, [T(A, 0, 2), T(B, 12, 14)], "inbound").unsure


def test_outbound_the_first_to_name_the_institute_is_the_agent():
    lines = _lines((B, 0.0, "الو نعم"), (A, 1.5, "معك المعهد السعودي للتدريب"))
    n = dz.name_speakers(lines, [T(B, 0, 1.4), T(A, 1.5, 4)], "outbound")
    assert (n.agent, n.reason) == (A, "says_institute")


def test_greeting_matches_whole_words_after_normalising():
    lines = _lines((A, 0.0, "أهلاً وسهلاً"), (B, 2.5, "معكوس"))                    # hamza / tanween folded
    assert dz.name_speakers(lines, TURNS, "inbound").agent == A
    assert dz.name_speakers(_lines((A, 0.0, "معكوس"), (B, 2.5, "تمام")), TURNS, "inbound").unsure


def test_label_names_lines_and_unsure_stays_unknown():
    lines = _lines((A, 0.0, "السلام عليكم"), (B, 2.5, "ابي"))
    assert [ln.speaker for ln in dz.label(lines, dz.Naming(A, "first_greets"))] == [dz.AGENT, dz.CUSTOMER]
    assert [ln.speaker for ln in dz.label(lines, dz.Naming(None, "no_greeting"))] == ["speaker_1", "speaker_2"]


# ---------- metrics ----------

def test_talk_ratio_interruptions_and_longest_wait():
    turns = [T(A, 0.0, 4.0),                 # agent 0-4
             T(B, 4.5, 10.0),                # customer 4.5-10
             T(A, 7.0, 9.5),                 # agent starts 2.5 s into the customer's turn, 2.5 s overlap = interruption
             T(A, 16.0, 18.0),               # agent answers 6 s after the customer stopped (longest wait)
             T(B, 18.1, 18.6),               # short backchannel: overlap < 1 s with nothing = no interruption
             T(A, 19.0, 20.0)]
    m = dz.metrics(turns, dz.Naming(A, "first_greets"))
    assert m.agent_talk_s == pytest.approx(4 + 2.5 + 2 + 1)
    assert m.customer_talk_s == pytest.approx(5.5 + 0.5)
    assert m.talk_ratio == pytest.approx(9.5 / 15.5, abs=1e-3)
    assert (m.interruptions_agent, m.interruptions_customer) == (1, 0)
    assert m.longest_wait_s == pytest.approx(6.0)


def test_metrics_when_unsure_have_no_ratio():
    m = dz.metrics([T(A, 0, 2), T(B, 1, 3)], dz.Naming(None, "no_greeting"))
    assert m.talk_ratio is None and m.agent_talk_s == 3.0 and m.interruptions_agent == 0


def test_split_call_end_to_end():
    words = [W("السلام", 0.2, 0.6), W("عليكم", 0.6, 1.0), W("معك", 1.0, 1.3), W("ابي", 2.6, 2.9),
             W("الفاتورة", 2.9, 3.5)]
    lines, naming, m = dz.split_call(words, [T(A, 0.0, 1.5), T(B, 2.5, 4.0)], "inbound")
    assert [(ln.speaker, ln.text) for ln in lines] == [(dz.AGENT, "السلام عليكم معك"), (dz.CUSTOMER, "ابي الفاتورة")]
    assert naming.agent == A and m.talk_ratio == pytest.approx(0.5)
    assert dz.rules_hash() and dz.Pyannote().version.startswith("pa31-84fd259-")
