"""Normaliser, WER/CER and chunking used by the ASR benchmark."""
import pytest

from callsight.asr.chunking import chunk_bounds, merge_texts
from callsight.asr.metrics import edit_distance, score
from callsight.asr.normalize import cap_repeats, normalize, prepare


@pytest.mark.parametrize("raw,expected", [
    ("السَّلامُ عَلَيْكُم", "السلام عليكم"),                 # harakat removed
    ("أهلاً وسهلاً، كيف الحال؟", "اهلا وسهلا كيف الحال"),   # alif forms + tanween + Arabic punctuation
    ("إن شاء الله", "ان شا الله"),                        # إ → ا, standalone hamza dropped
    ("آخر مسألة", "اخر مسالة".replace("ة", "ه")),         # آ → ا, أ → ا, ة → ه
    ("مؤسسة", "موسسه"),                                   # ؤ → و
    ("مسائل", "مسايل"),                                   # ئ → ي
    ("على", "على"),                                       # ى is NOT folded
    ("رقم ٠٥٥١٢٣", "رقم 055123"),                         # Eastern digits
    ("ڤيزا پنك", "فيزا بنك"),
    ("طـــيب", "طيب"),                                    # tatweel
    ("OK  تمام!!", "ok تمام"),                            # Latin casefold, spaces, ASCII punctuation
])
def test_normalize_rules(raw, expected):
    assert normalize(raw) == expected


def test_cap_repeats_cuts_long_loops_only():
    assert cap_repeats(["نعم"] * 15) == ["نعم"] * 10
    assert cap_repeats(["نعم"] * 10) == ["نعم"] * 10
    assert cap_repeats(["يا", "هلا"] * 12) == ["يا", "هلا"] * 10
    assert cap_repeats(["a", "b", "c"]) == ["a", "b", "c"]


def test_edit_distance():
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance([], ["a"]) == 1
    assert edit_distance(["a", "b"], ["a", "b"]) == 0


def test_wer_counts_sub_del_ins():
    s = score([("واحد اثنين ثلاثة اربعة", "واحد اتنين ثلاثة")])   # 1 substitution + 1 deletion
    assert s.ref_words == 4 and s.word_edits == 2 and s.wer == pytest.approx(0.5)


def test_wer_is_corpus_level_micro_average():
    s = score([("a b c d e f g h i j", "a b c d e f g h i j"), ("x y", "")])
    assert s.wer == pytest.approx(2 / 12)          # not the mean of 0% and 100%


def test_normalisation_differences_are_not_errors():
    assert score([("أهلاً بك في الشركة", "اهلا بك في الشركه")]).wer == 0


def test_empty_hypothesis_is_full_deletion():
    assert score([("كلمة واحدة", "")]).wer == 1.0


def test_hallucination_loop_is_capped():
    s = score([("نعم", " ".join(["نعم"] * 100))])
    assert s.word_edits == 9                         # 10 copies kept -> 9 insertions, not 99


def test_prepare():
    assert prepare("  مرحباً،   كيفك؟ ") == ["مرحبا", "كيفك"]


def test_chunk_bounds_cover_audio_with_overlap():
    rate = 16000
    b = chunk_bounds(60 * rate, rate)
    assert b[0] == (0, 30 * rate)
    assert b[1][0] == 29 * rate                    # 1 s overlap
    assert b[-1][1] == 60 * rate
    assert all(e - s <= 30 * rate for s, e in b)


def test_chunk_bounds_short_and_empty():
    assert chunk_bounds(10 * 8000, 8000) == [(0, 80000)]
    assert chunk_bounds(0, 8000) == []
    with pytest.raises(ValueError):
        chunk_bounds(100, 10, window_s=1, overlap_s=1)


def test_merge_texts_drops_repeated_overlap():
    assert merge_texts(["كيف اقدر اساعدك اليوم", "اساعدك اليوم يا استاذ"]) == "كيف اقدر اساعدك اليوم يا استاذ"
    assert merge_texts(["one two", "three four"]) == "one two three four"
    assert merge_texts(["", "a b", ""]) == "a b"
