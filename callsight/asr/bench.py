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
import sys
import time
import wave
from array import array
from pathlib import Path

from .chunking import chunk_bounds, merge_texts

TARGET_RATE = 16000


# ---------- helpers without GPU (tested) ----------

def list_clips(gold_dir: Path) -> list[Path]:
    if not gold_dir.is_dir():
        raise FileNotFoundError(f"gold set folder not found: {gold_dir}")
    return sorted(p for p in gold_dir.iterdir() if p.is_file() and p.suffix == ".wav")


def read_wav(path: Path) -> tuple[array, int]:
    """16-bit mono PCM samples and the sample rate."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"{path.name}: expected 16-bit mono WAV")
        rate, frames = w.getframerate(), w.readframes(w.getnframes())
    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples, rate


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


# ---------- model adapters (GPU, imported lazily) ----------

class WhisperLargeV3:
    """openai/whisper-large-v3 via transformers, fp16, language forced to Arabic."""
    name = "whisper-large-v3"
    repo = "openai/whisper-large-v3"
    revision = "06f233fe06e710322aca913c1bc4249a0d71fce1"     # pinned 2026-11-14
    window_s = 30.0

    def load(self):
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(self.repo, revision=self.revision)
        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
            self.repo, revision=self.revision, dtype=torch.float16, low_cpu_mem_usage=True).to("cuda").eval()

    def transcribe(self, audio16k) -> str:
        feats = self.processor(audio16k, sampling_rate=TARGET_RATE, return_tensors="pt").input_features
        feats = feats.to("cuda", dtype=self.torch.float16)
        with self.torch.inference_mode():
            ids = self.model.generate(feats, language="ar", task="transcribe", num_beams=1, max_new_tokens=440)
        return self.processor.batch_decode(ids, skip_special_tokens=True)[0].strip()


class AudarTurbo:
    """audarai/Audar-ASR-V1-Turbo via the qwen-asr package, bf16, settings from the model card (benchmark settings)."""
    name = "audar-asr-turbo"
    repo = "audarai/Audar-ASR-V1-Turbo"
    revision = "371428bea487c7aec82b27dc21f8d4324002e98e"     # pinned 2026-11-14
    window_s = 30.0                                          # audio encoder context; longer audio chunked by us

    def load(self):
        import torch
        from qwen_asr import Qwen3ASRModel
        self.model = Qwen3ASRModel.from_pretrained(self.repo, revision=self.revision, dtype=torch.bfloat16, device_map="cuda:0",
                                                   max_inference_batch_size=1, max_new_tokens=256)

    def transcribe(self, audio16k) -> str:
        result = self.model.transcribe(audio=[(audio16k, TARGET_RATE)], language=["Arabic"])[0]
        return (result.text or "").strip()


class JaisAsr4bit:
    """IFM/Jais-ASR-W4A16-AWQ (official 4-bit; bf16 needs ~12 GB) via transformers 5 + compressed-tensors."""
    name = "jais-asr-4bit"
    repo = "IFM/Jais-ASR-W4A16-AWQ"
    revision = "31b942fd9bd6278635acc19c57393f9442d22252"     # pinned 2026-11-14: remote code can't change silently
    window_s = 30.0                                          # card gives no limit; same windows as the others

    def load(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(self.repo, revision=self.revision, trust_remote_code=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            self.repo, revision=self.revision, trust_remote_code=True, device_map="cuda:0", dtype=torch.bfloat16).eval()
        self.prompt = self.processor.tokenizer.apply_chat_template(
            [{"role": "user", "content": [{"type": "audio"}]}], tokenize=False, add_generation_prompt=True)

    def transcribe(self, audio16k) -> str:
        inputs = self.processor(text=[self.prompt], audio=[audio16k], sampling_rate=[TARGET_RATE],
                                padding=True, return_tensors="pt").to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(**inputs, do_sample=False, max_new_tokens=256)
        r = self.processor.post_process_transcription(out, input_lengths=inputs["input_ids"].shape[1])[0]
        return "" if getattr(r, "refused", False) else (r.text or "").strip()


MODELS = {m.name: m for m in (WhisperLargeV3, AudarTurbo, JaisAsr4bit)}


def to_16k(samples: array, rate: int):
    import numpy as np
    import soxr
    x = np.frombuffer(samples.tobytes(), dtype=np.int16).astype(np.float32) / 32768.0
    return x if rate == TARGET_RATE else soxr.resample(x, rate, TARGET_RATE)


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
