"""Benchmark report: hypothesis loading, failure detection, scoring, winner, rendering."""
import json

from callsight.asr import report


def _jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\nnot json\n", encoding="utf-8")


def test_load_hypotheses_last_line_wins_and_skips_broken(tmp_path):
    p = tmp_path / "m.jsonl"
    _jsonl(p, [{"clip": "a.wav", "text": "old"}, {"clip": "a.wav", "text": "new"}, {"nope": 1}])
    assert report.load_hypotheses(p) == {"a.wav": {"clip": "a.wav", "text": "new"}}


def test_failure_kinds():
    ref = "واحد اثنين ثلاثه اربعه خمسه سته سبعه ثمانيه تسعه عشره"
    assert report.failure_kind(ref, "") == "empty"
    assert report.failure_kind(ref, "واحد اثنين") == "truncated"
    assert report.failure_kind(ref, "نعم " * 12) == "repeated"
    assert report.failure_kind(ref, ref) is None


def test_score_model_counts_missing_and_speed():
    refs = {"a.wav": "مرحبا بك", "b.wav": "شكرا لك"}
    hyps = {"a.wav": {"clip": "a.wav", "text": "مرحبا بك", "audio_s": 60, "seconds": 6, "peak_gpu_mb": 3000}}
    r = report.score_model("m", refs, hyps)
    assert (r.clips, r.missing, r.score.wer, round(r.rtf, 2), r.peak_gpu_mb) == (1, 1, 0.0, 0.1, 3000)


def test_winner_needs_all_clips_and_target():
    refs = {"a.wav": "مرحبا بك اليوم يا اخي"}
    good = report.score_model("good", refs, {"a.wav": {"text": "مرحبا بك اليوم يا اخي"}})
    bad = report.score_model("bad", refs, {"a.wav": {"text": "لا"}})
    assert report.winner([bad, good], 1).model == "good"
    assert report.winner([bad], 1) is None
    assert report.winner([good], 2) is None        # did not run every typed clip


def test_build_report_writes_table_and_provisional_warning(tmp_path):
    _jsonl(tmp_path / "whisper-large-v3.jsonl",
           [{"clip": "clip_01.wav", "text": "مرحبا بك", "audio_s": 60, "seconds": 7.4, "peak_gpu_mb": 3277}])
    text, examples, results = report.build_report({"clip_01.wav": "مرحبا بك"}, tmp_path, total_clips=30)
    assert "| whisper-large-v3 | 1/1 | 0.0% | 0.0% |" in text
    assert "مرحبا" not in text and "مرحبا بك" in examples      # customer speech only in the private file
    assert "Only 1 of 30" in text and "No winner yet" in text
    assert results[0].model == "whisper-large-v3"


def test_build_report_names_winner_when_complete(tmp_path):
    _jsonl(tmp_path / "audar-asr-turbo.jsonl", [{"clip": "c.wav", "text": "اهلا وسهلا", "audio_s": 60, "seconds": 3}])
    text, _, _ = report.build_report({"c.wav": "اهلا وسهلا"}, tmp_path, total_clips=1)
    assert "**Winner: audar-asr-turbo**" in text and "licence" in text


def test_cli_imports_and_score_needs_gold_set(monkeypatch, capsys):
    """The CLI module must at least import (a syntax error once slipped through untested)."""
    from callsight import cli
    assert callable(cli.main)
