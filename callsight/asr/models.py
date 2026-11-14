"""Speech-to-text model adapters + audio input, shared by the benchmark and the asr worker.

GPU libraries (torch, transformers, qwen-asr, numpy, soxr) are imported lazily inside the methods, so this module
imports anywhere (ingest worker, API, tests) without them. Each adapter: `load()`, then `transcribe(audio16k) -> str`
for one window of at most `window_s` seconds.
"""
from __future__ import annotations

import sys
import wave
from array import array
from pathlib import Path

TARGET_RATE = 16000


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


