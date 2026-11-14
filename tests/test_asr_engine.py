"""Transcription engine with a fake model adapter (no GPU, no numpy): windows, display times, silence skip,
hallucination filter, merge, audio errors, idle unload."""
import math
import wave

import pytest

from callsight.asr import engine
from callsight.asr.models import TARGET_RATE

RATE = TARGET_RATE


def tone(seconds: float, amp: float = 0.1) -> list[float]:
    """A loud-enough signal (not silence) as plain floats at 16 kHz."""
    return [amp * math.sin(i * 0.3) for i in range(int(seconds * RATE))]


class FakeAdapter:
    """Returns a different short text per window; records how many windows it saw."""
    name = "fake"
    revision = "rev1"
    window_s = 30.0

    def __init__(self, texts=None):
        self.calls = 0
        self.texts = texts
        self.loaded = False

    def load(self):
        self.loaded = True

    def transcribe(self, audio16k) -> str:
        self.calls += 1
        if self.texts is not None:
            return self.texts[self.calls - 1]
        return f"جملة رقم {self.calls}"


def test_three_minute_call_gives_seven_windows_with_clean_times():
    a = FakeAdapter()
    r = engine.transcribe_audio(a, tone(180), RATE)
    assert len(r.segments) == math.ceil((180 - 1) / 29) == 7 == a.calls
    times = [(s.start, s.end) for s in r.segments]
    assert times[0][0] == 0.0 and times[-1][1] == pytest.approx(180.0)
    for (s1, e1), (s2, e2) in zip(times, times[1:]):
        assert s1 < e1 == s2 < e2                    # increasing, touching, never overlapping
    assert all(s.text and s.flag is None for s in r.segments)
    assert r.text and r.audio_seconds == pytest.approx(180.0) and r.dropped_windows == 0
    assert r.speech_seconds == pytest.approx(180.0)


def test_boundary_sits_in_the_middle_of_the_overlap():
    bounds = [(0, 30 * RATE), (29 * RATE, 59 * RATE)]
    assert engine.display_times(bounds, RATE) == [(0.0, 29.5), (29.5, 59.0)]


def test_short_call_one_window():
    r = engine.transcribe_audio(FakeAdapter(), tone(12), RATE)
    assert len(r.segments) == 1 and r.segments[0].end == pytest.approx(12.0)


def test_silent_window_skipped_without_calling_the_model():
    a = FakeAdapter()
    audio = tone(29) + [0.0] * (31 * RATE)          # 60 s: speech, then silence
    r = engine.transcribe_audio(a, audio, RATE)
    flags = [s.flag for s in r.segments]
    assert "silence" in flags and flags[0] is None
    assert a.calls == len(r.segments) - flags.count("silence")
    assert r.dropped_windows == flags.count("silence")
    assert all(s.text == "" for s in r.segments if s.flag)
    assert r.speech_seconds < r.audio_seconds


@pytest.mark.parametrize("text,flag", [
    ("اشتركوا في القناة", "blocklist"),
    ("شكراً للمشاهدة", "blocklist"),                      # harakat/tanween ignored
    ("ترجمة نانسي قنقر", "blocklist"),
    ("نعم نعم نعم نعم نعم", "repeat"),                     # 1 word 5x in a row
    ("طيب تمام " * 5, "repeat"),                            # 2-word unit 5x in a row
    ("ا" * 400, "too_fast"),                                # 400 characters in ~10 s
    ("السلام عليكم معك خدمة العملاء كيف اقدر اساعدك", None),
    ("نعم نعم نعم نعم", None),                             # 4x is still allowed
])
def test_hallucination_filter(text, flag):
    assert engine.hallucination_flag(text, 10.0) == flag


def test_dropped_window_keeps_flag_and_empty_text_and_is_left_out_of_full_text():
    a = FakeAdapter(texts=["السلام عليكم", "اشتركوا في القناة", "مع السلامة"])
    r = engine.transcribe_audio(a, tone(80), RATE)
    assert [s.flag for s in r.segments] == [None, "blocklist", None]
    assert r.segments[1].text == ""
    assert r.text == "السلام عليكم مع السلامة" and r.dropped_windows == 1


def test_overlap_words_merged_once():
    a = FakeAdapter(texts=["كيف اقدر اساعدك", "اساعدك في الطلب"])
    r = engine.transcribe_audio(a, tone(50), RATE)
    assert r.text == "كيف اقدر اساعدك في الطلب"


def test_model_error_propagates():
    class Broken(FakeAdapter):
        def transcribe(self, audio16k):
            raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        engine.transcribe_audio(Broken(), tone(5), RATE)


def _wav(path, seconds=1.0, rate=8000, channels=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate) * channels)


def fake_resample(samples, rate):
    return [s / 32768 for s in samples for _ in range(RATE // rate)]


def test_load_audio_missing_file(tmp_path):
    with pytest.raises(engine.AudioError, match="not found"):
        engine.load_audio(tmp_path / "nope.wav", resample=fake_resample)


def test_load_audio_broken_file(tmp_path):
    p = tmp_path / "bad.wav"
    p.write_bytes(b"not a wav at all")
    with pytest.raises(engine.AudioError, match="WAV"):
        engine.load_audio(p, resample=fake_resample)


def test_load_audio_stereo_refused(tmp_path):
    p = tmp_path / "st.wav"
    _wav(p, channels=2)
    with pytest.raises(engine.AudioError):
        engine.load_audio(p, resample=fake_resample)


def test_load_audio_empty(tmp_path):
    p = tmp_path / "empty.wav"
    _wav(p, seconds=0)
    with pytest.raises(engine.AudioError, match="empty"):
        engine.load_audio(p, resample=fake_resample)


def test_load_audio_ok_8k_to_16k(tmp_path):
    p = tmp_path / "ok.wav"
    _wav(p, seconds=0.5)
    assert len(engine.load_audio(p, resample=fake_resample)) == RATE // 2


def test_model_holder_loads_once_and_unloads_when_idle():
    made = []

    def factory(name):
        made.append(FakeAdapter())
        return made[-1]

    t = [100.0]
    h = engine.ModelHolder("fake", idle_unload_s=300, factory=factory, clock=lambda: t[0], start_timer=False)
    with h.use() as a1:
        assert a1.loaded
    with h.use() as a2:
        assert a2 is a1 and len(made) == 1
    t[0] += 299
    assert not h.unload_if_idle() and h.loaded
    t[0] += 2
    assert h.unload_if_idle() and not h.loaded
    with h.use() as a3:
        assert a3 is not a1 and len(made) == 2


def test_model_holder_never_unloads_while_busy():
    t = [0.0]
    h = engine.ModelHolder("fake", idle_unload_s=1, factory=lambda n: FakeAdapter(), clock=lambda: t[0],
                           start_timer=False)
    with h.use():
        t[0] += 100
        assert not h.unload_if_idle() and h.loaded


def test_model_holder_unknown_model():
    with pytest.raises(ValueError, match="ASR_MODEL"):
        engine.check_model_name("no-such-model")
    with pytest.raises(engine.ModelNotReady):
        engine.ModelHolder("no-such-model", start_timer=False).get()


def test_failed_load_is_not_ready_and_backs_off():
    tries = []

    class NoCache(FakeAdapter):
        def load(self):
            tries.append(1)
            raise OSError("not in the offline cache")
    t = [0.0]
    h = engine.ModelHolder("fake", factory=lambda n: NoCache(), clock=lambda: t[0], start_timer=False,
                           load_retry_s=120)
    with pytest.raises(engine.ModelNotReady, match="OSError"):
        h.ensure_loaded()
    for _ in range(2):
        with pytest.raises(engine.ModelNotReady, match="recently"):
            h.ensure_loaded()
    assert len(tries) == 1                               # no new slow load during the back-off
    t[0] += 121
    with pytest.raises(engine.ModelNotReady):
        h.ensure_loaded()
    assert len(tries) == 2 and not h.loaded


def test_no_overlap_merge_across_a_dropped_window():
    a = FakeAdapter(texts=["كيف اقدر اساعدك", "اشتركوا في القناة", "اساعدك في الطلب"])
    r = engine.transcribe_audio(a, tone(80), RATE)
    assert r.text == "كيف اقدر اساعدك اساعدك في الطلب"     # both "اساعدك" are real speech


def test_nul_characters_removed():
    r = engine.transcribe_audio(FakeAdapter(texts=["مرحبا\x00 بك"]), tone(5), RATE)
    assert r.segments[0].text == "مرحبا بك" and "\x00" not in r.text
