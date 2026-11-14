"""Bench runner helpers that need no GPU: clip listing, resume, output records."""
import json
import wave

import pytest

from callsight.asr import bench


def _wav(path, seconds=1.0, rate=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))


def test_list_clips_sorted_wav_only(tmp_path):
    for name in ("clip_02_call_9.wav", "clip_01_call_5.wav", "notes.txt", "clip_03_call_1.wav.part"):
        (tmp_path / name).write_bytes(b"")
    assert [p.name for p in bench.list_clips(tmp_path)] == ["clip_01_call_5.wav", "clip_02_call_9.wav"]


def test_list_clips_missing_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        bench.list_clips(tmp_path / "nope")


def test_read_wav_mono_16bit(tmp_path):
    p = tmp_path / "a.wav"
    _wav(p, seconds=0.5, rate=8000)
    samples, rate = bench.read_wav(p)
    assert rate == 8000 and len(samples) == 4000


def test_done_clips_reads_existing_jsonl_and_ignores_broken_lines(tmp_path):
    out = tmp_path / "m.jsonl"
    out.write_text(json.dumps({"clip": "clip_01_call_5.wav", "text": "x"}) + "\n{broken\n", encoding="utf-8")
    assert bench.done_clips(out) == {"clip_01_call_5.wav"}
    assert bench.done_clips(tmp_path / "missing.jsonl") == set()


def test_append_record_is_utf8_one_line(tmp_path):
    out = tmp_path / "sub" / "m.jsonl"
    bench.append_record(out, {"clip": "c.wav", "text": "السلام عليكم\nمرحبا"})
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["text"] == "السلام عليكم\nمرحبا"


def test_unknown_model_rejected():
    with pytest.raises(SystemExit):
        bench.main(["--model", "no-such-model"])


def test_done_clips_retries_errored(tmp_path):
    out = tmp_path / "m.jsonl"
    out.write_text('{"clip": "a.wav", "text": "x"}\n{"clip": "b.wav", "text": "", "error": "OOM"}\n',
                   encoding="utf-8")
    assert bench.done_clips(out) == {"a.wav"}


def test_limit_must_be_positive():
    with pytest.raises(SystemExit):
        bench.main(["--model", "whisper-large-v3", "--limit", "0"])
