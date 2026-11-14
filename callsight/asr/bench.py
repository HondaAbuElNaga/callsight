"""Run one speech-to-text model on the gold-set clips (inside the GPU `asr-bench` container).

    python -m callsight.asr.bench --check                      # GPU visible?
    python -m callsight.asr.bench --model whisper-large-v3 [--limit 1]

Writes one JSON line per clip to OUT_DIR/<model>.jsonl (hypothesis, timings, peak GPU memory) and skips clips
already in that file, so a stopped run can continue. Audio never leaves the container: models are only downloaded.
Scoring against the typed reference texts happens later, outside this container.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .chunking import chunk_bounds, merge_texts
from .models import MODELS, TARGET_RATE, read_wav, to_16k  # noqa: F401  (read_wav used by tests)


# ---------- helpers without GPU (tested) ----------

def list_clips(gold_dir: Path) -> list[Path]:
    if not gold_dir.is_dir():
        raise FileNotFoundError(f"gold set folder not found: {gold_dir}")
    return sorted(p for p in gold_dir.iterdir() if p.is_file() and p.suffix == ".wav")


def done_clips(out: Path) -> set[str]:
    if not out.exists():
        return set()
    done = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            if "error" not in rec:                   # failed clips (e.g. CUDA out of memory) run again
                done.add(rec["clip"])
        except (ValueError, KeyError, TypeError):
            continue
    return done


def append_record(out: Path, record: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def check_gpu() -> int:
    import torch
    ok = torch.cuda.is_available()
    print(f"torch {torch.__version__}, CUDA build {torch.version.cuda}, GPU available: {ok}")
    if ok:
        free, total = torch.cuda.mem_get_info()
        print(f"GPU: {torch.cuda.get_device_name(0)}, capability {torch.cuda.get_device_capability(0)}, "
              f"memory {total / 2**30:.1f} GB ({free / 2**30:.1f} GB free)")
        torch.ones(4, device="cuda").sum().item()      # fails here if the build lacks kernels for this GPU
        print("CUDA kernel test: ok")
    return 0 if ok else 1


def run(model_name: str, gold_dir: Path, out_dir: Path, limit: int | None) -> int:
    import torch
    clips = list_clips(gold_dir)
    out = out_dir / f"{model_name}.jsonl"
    done = done_clips(out)
    todo = [c for c in clips if c.name not in done]
    if limit is not None:
        todo = todo[:limit]
    print(f"{model_name}: {len(clips)} clips, {len(done)} already done, {len(todo)} to run -> {out}")
    if not todo:
        return 0

    adapter = MODELS[model_name]()
    t0 = time.perf_counter()
    adapter.load()
    load_s = time.perf_counter() - t0
    print(f"model loaded in {load_s:.0f} s")

    errors_in_row = 0
    for clip in todo:
        if errors_in_row >= 3:
            print("3 errors in a row: stopping (GPU state is probably broken); run again to retry")
            return 1
        torch.cuda.reset_peak_memory_stats()
        samples, rate = read_wav(clip)
        audio = to_16k(samples, rate)
        record = {"clip": clip.name, "model": model_name, "audio_s": round(len(audio) / TARGET_RATE, 2),
                  "load_s": round(load_s, 1)}
        t = time.perf_counter()
        try:
            parts = [adapter.transcribe(audio[s:e])
                     for s, e in chunk_bounds(len(audio), TARGET_RATE, adapter.window_s, 1.0)]
            torch.cuda.synchronize()
            record["text"] = merge_texts(parts)
            record["chunks"] = len(parts)
            errors_in_row = 0
        except Exception as exc:                             # one bad clip must not stop the run
            record["text"], record["error"] = "", f"{type(exc).__name__}: {exc}"[:300]
            errors_in_row += 1
        record["seconds"] = round(time.perf_counter() - t, 2)
        record["peak_gpu_mb"] = round(torch.cuda.max_memory_allocated() / 2**20)
        append_record(out, record)
        print(f"  {clip.name}: {record['seconds']} s, {record['peak_gpu_mb']} MB, "
              f"{len(record['text'].split())} words{' ERROR ' + record['error'] if 'error' in record else ''}")
    return 0


def _positive(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m callsight.asr.bench")
    ap.add_argument("--model", choices=sorted(MODELS))
    ap.add_argument("--check", action="store_true", help="only show whether the GPU works")
    ap.add_argument("--limit", type=_positive, help="run at most this many clips (smoke test)")
    ap.add_argument("--gold-dir", type=Path, default=Path(os.environ.get("AUDIO_DIR", "/data/audio")) / "goldset")
    ap.add_argument("--out-dir", type=Path, default=Path(os.environ.get("BENCH_OUT", "/out")))
    args = ap.parse_args(argv)
    if args.check:
        return check_gpu()
    if not args.model:
        ap.error("--model or --check is required")
    return run(args.model, args.gold_dir, args.out_dir, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
