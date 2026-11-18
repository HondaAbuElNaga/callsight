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


def is_time_limit(exc: BaseException) -> bool:
    """Celery's soft time limit (an Exception subclass): "never fail" handlers must let it through."""
    return type(exc).__name__ == "SoftTimeLimitExceeded"


BAD_CHAR = "�"                      # a half-decoded UTF-8 letter (its other byte is in the next token)


def token_words(tokenizer, seq: list[int], times: list[float], text: str,
                duration: float) -> list[tuple[str, float, float]]:
    """Generated tokens + their start times -> [(word, start, end)] whose words equal `text.split()`.
    A token's characters are found by decoding growing prefixes (Arabic letters may span two byte tokens).
    Anything inconsistent gives [] for the window (the window then has text but no speaker), never wrong times."""
    special = set(tokenizer.all_special_ids)
    pairs = [(t, float(ts)) for t, ts in zip(seq, times) if t not in special]
    if not pairs or not text:
        return []
    ids = [t for t, _ in pairs]
    ends, prev = [], ""
    for k in range(1, len(ids) + 1):
        cur = tokenizer.decode(ids[:k], skip_special_tokens=True)
        if not cur.startswith(prev.rstrip(BAD_CHAR)):
            return []
        ends.append(len(cur))
        prev = cur
    full = prev
    if full.strip() != text or BAD_CHAR in full:
        return []
    words, pos = [], 0
    for w in text.split():
        start_c = full.index(w, pos)
        end_c = start_c + len(w)
        pos = end_c
        first = next(i for i, e in enumerate(ends) if e > start_c)
        last = next(i for i, e in enumerate(ends) if e >= end_c)
        t0 = pairs[first][1]
        t1 = pairs[last + 1][1] if last + 1 < len(pairs) else min(duration, pairs[last][1] + 0.5)
        words.append((w, round(t0, 3), round(max(t1, t0 + 0.02), 3)))
    return words


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

    def transcribe_words(self, audio16k) -> tuple[str, list[tuple[str, float, float]]]:
        """The decode of `transcribe` (byte-identical text) + word times (spec 2026-11-17-diarization, decision A).
        generate(return_token_timestamps=True) would switch attention to "eager", whose fp16 rounding changed the
        text of 1 window in 8 test clips (2026-11-19). So: normal decode first, then ONE teacher-forced eager pass
        over that same token sequence for the cross-attentions, then transformers' own DTW (alignment heads, median
        filter), cropped to the real audio length. Times are seconds from the window start."""
        feats = self.processor(audio16k, sampling_rate=TARGET_RATE, return_tensors="pt").input_features
        feats = feats.to("cuda", dtype=self.torch.float16)
        with self.torch.inference_mode():
            ids = self.model.generate(feats, language="ar", task="transcribe", num_beams=1, max_new_tokens=440)
        text = self.processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
        seq = ids[0].tolist()
        try:
            times = self.token_times(feats, ids, len(audio16k))
        except Exception as exc:                           # noqa: BLE001  no times -> window without speakers
            if is_time_limit(exc):
                raise                                      # the task's 15 min limit must still end the task
            return text, []
        return text, token_words(self.processor.tokenizer, seq, times, text, len(audio16k) / TARGET_RATE)

    def token_times(self, feats, ids, n_samples: int | None, prompt_len: int | None = None) -> list[float]:
        """Start time (s) of every token of `ids` (1 x L, as generate returned it). n_samples=None: no crop (the
        same matrix as generate(return_token_timestamps=True), used by the check script)."""
        import numpy as np
        from transformers.models.whisper.generation_whisper import _dynamic_time_warping, _median_filter
        torch, m = self.torch, self.model
        special = set(self.processor.tokenizer.all_special_ids)
        seq = ids[0].tolist()
        if prompt_len is None:                             # <|startoftranscript|><|ar|><|transcribe|><|notimestamps|>
            prompt_len = next((i for i, t in enumerate(seq) if t not in special), len(seq))
        if len(seq) - 1 <= prompt_len:
            return [0.0] * len(seq)
        before = m.config._attn_implementation
        try:
            m.config._attn_implementation = "eager"        # attention weights only exist in eager
            with torch.inference_mode():
                out = m(input_features=feats, decoder_input_ids=ids[:, :-1], output_attentions=True,
                        use_cache=False, return_dict=True)
        finally:
            m.config._attn_implementation = before
        cross = out.cross_attentions                       # per layer: (1, heads, L-1, 1500)
        w = torch.stack([cross[layer][0, head] for layer, head in m.generation_config.alignment_heads]).float()
        w = w[:, prompt_len:, :]                           # rows of the text tokens (as transformers does)
        if n_samples is not None:
            w = w[:, :, : max(1, n_samples // 160 // 2)]   # 160 samples per mel frame, 2 frames per position
        std = torch.std(w, dim=-2, keepdim=True, unbiased=False)
        mean = torch.mean(w, dim=-2, keepdim=True)
        w = _median_filter(((w - mean) / std).unsqueeze(0), m.config.median_filter_width)[0]
        text_idx, time_idx = _dynamic_time_warping(-w.mean(dim=0).cpu().double().numpy())
        jumps = np.pad(np.diff(text_idx), (1, 0), constant_values=1).astype(bool)
        jump_times = (time_idx[jumps] * 0.02).tolist()
        return [0.0] * prompt_len + jump_times + [jump_times[-1]]


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


