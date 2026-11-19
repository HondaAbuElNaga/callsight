# Speaker split (who said what): how it works, step by step

This is **step 4** of the finish plan (spec `specs/2026-11-17-diarization`). It splits each call transcript into
**الموظف** (agent) lines and **العميل** (customer) lines, and measures talk %, interruptions and waiting.

The recording is **mono**: both voices are mixed in one track. So a program has to work out who speaks when.

---

## The big picture

```
recording (.wav)
   │
   ├─► 1. Whisper writes the text            "السلام عليكم معك سارة ابي الفاتورة"
   ├─► 2. word times                         السلام 0.2–0.6 s, عليكم 0.6–1.0 s, ...
   ├─► 3. remove the double words of the 1 s window overlap
   ├─► 4. pyannote: who speaks when          SPEAKER_00 0.0–1.5 s, SPEAKER_01 2.5–4.0 s
   ├─► 5. each word → its speaker → lines    SPEAKER_00: "السلام عليكم معك سارة"
   ├─► 6. who is the agent? (the greeting)   SPEAKER_00 = agent
   ├─► 7. numbers: talk %, interruptions, longest wait
   ├─► 8. save in the database
   └─► 9. show on the page, in the .txt and on the KPI page
```

Everything runs on the **GPU server**, right after Whisper, in the same job.

---

## Step 1: Whisper writes the text (unchanged)

**File:** `callsight/asr/models.py` → `WhisperLargeV3.transcribe_words()`

- The call is cut into windows of up to 30 s, and neighbouring windows overlap by 1 s.
- Whisper writes each window **exactly as before**. This keeps the accuracy we measured (WER) valid.

**Example:** window 1 → `"السلام عليكم معك سارة ابي الفاتورة"`

> **Lesson learned:** asking Whisper for word times in the same run changed the text a little. On 1 window out of
> 8 test clips, 3 words were different. So the code writes the text first, then measures the times in a separate
> second pass.

---

## Step 2: a time for every word

**File:** `callsight/asr/models.py` → `token_times()` and `token_words()`

1. Whisper writes in small pieces called *tokens*. A word can be 1–3 tokens.
2. `token_times()` runs the model **once more** over the finished text and reads where it was "looking" in the
   audio for each token (cross-attention and DTW, the same method transformers uses).
3. `token_words()` glues the tokens back into whole words and gives each word a start and an end time.

**Example:**

```
tokens:  " ال" (0.10 s)   "سلام" (0.30 s)   " عليكم" (0.62 s)   " معك" (1.10 s)
words:   ("السلام", 0.10, 0.62)  ("عليكم", 0.62, 1.10)  ("معك", 1.10, ...)
```

If anything does not match, the window gets **no times**, never wrong times. It is then shown as one normal line
with no speaker.

---

## Step 3: count the overlap words only once

**File:** `callsight/asr/engine.py` → `window_words()` and `transcribe_audio()`

Windows overlap by 1 s, so a word near the edge can appear in two windows. A word belongs to the window whose
display range holds the **middle** of the word.

**Example:** window 2 starts at 29.0 s, and the boundary between the windows is at 29.5 s.

```
word "قبل" at 29.1–29.4 s → middle 29.25 → belongs to window 1 (not counted again in window 2)
word "بعد" at 29.6–30.0 s → middle 29.8  → belongs to window 2
```

If the next window is silent or dropped, the window keeps its edge words, so no word is lost.

---

## Step 4: pyannote finds who speaks when

**File:** `callsight/asr/diarize.py` → class `Pyannote`

- Model: **pyannote/speaker-diarization-3.1**. It runs on our own GPU, with no internet and no outside API.
- We tell it: **exactly 2 speakers**.
- It returns "turns", i.e. pieces of time with an anonymous label:

```
SPEAKER_00  0.0 – 1.5 s
SPEAKER_01  2.5 – 4.0 s
SPEAKER_00  5.5 – 8.0 s
```

It does not know names. It only knows "voice A" and "voice B".

---

## Step 5: each word goes to its speaker, and neighbouring words become lines

**File:** `callsight/asr/diarize.py` → `assign()`

- A word goes to the turn that overlaps it most. If no turn overlaps it, it goes to the nearest turn.
- Words in a row from the same speaker become one line.

**Example:**

```
words:  السلام(0.2) عليكم(0.6) معك(1.0) | ابي(2.6) الفاتورة(2.9)
turns:  SPEAKER_00 0.0–1.5            | SPEAKER_01 2.5–4.0
lines:  SPEAKER_00: "السلام عليكم معك"
        SPEAKER_01: "ابي الفاتورة"
```

---

## Step 6: who is the agent?

**File:** `callsight/asr/diarize.py` → `name_speakers()` and `label()`

| Call type | Rule |
|---|---|
| Incoming | The **first** speaker, if they say a greeting in the first 10 s ("السلام عليكم", "معك", "المعهد السعودي", "حياك"...) |
| Outgoing | The first speaker to say the institute's name or a greeting |
| Neither rule works | **Unsure**: the lines show "متحدث 1 / متحدث 2" (we never guess) |

**Example:** SPEAKER_00 said "السلام عليكم معك" at 0.2 s → SPEAKER_00 = **الموظف**, SPEAKER_01 = **العميل**.

The greeting words are in the `GREETINGS` list at the top of `diarize.py`. Spelling differences (أ/ا, ة/ه,
tanween) are ignored.

---

## Step 7: the numbers

**File:** `callsight/asr/diarize.py` → `metrics()`

| Number | How it is measured | Example |
|---|---|---|
| Talk % | agent speaking seconds ÷ everyone's speaking seconds | agent 9.5 s, customer 6 s → 61 % |
| Interruption | one person starts more than 0.3 s after the other began, while the other is still talking, and both talk together for more than 1 s ("اها" under 1 s does not count) | agent cuts in at 7.0 s while the customer talks 4.5–10 s → 1 |
| Longest wait | the longest silence after the customer stops, before anyone speaks | customer stops at 10 s, agent answers at 16 s → 6 s |

These are measured from the pyannote turns, not from the word times, because the turns are more exact.

---

## Step 8: save in the database (migration 0024)

**Files:** `callsight/db/migrations/versions/0024_diarization.py`, `callsight/db/models.py`, and
`callsight/asr/tasks.py` → `save_speakers()`

| Where | What | Example |
|---|---|---|
| `transcript_segment.words` | word times of each window | `[["السلام", 0.2, 0.6], ["عليكم", 0.6, 1.0]]` |
| `speaker_turn` (new table) | pyannote's raw turns | `SPEAKER_00, 0.0, 1.5` |
| `transcript_turn` (new table) | the lines shown to people | `agent, 0.2, 1.3, "السلام عليكم معك"` |
| `transcript` (new columns) | the numbers, and whether the agent could be named | `talk_ratio 0.61, speaker_unsure false` |

Safety rules:
- If pyannote is missing or fails, the transcript is **still saved as before**, just without speakers.
- Deleting a call's content also deletes all of these rows.
- Logs never contain call text.

---

## Step 9: show it

**Files:** `callsight/transcripts.py`, `callsight/api/main.py`, `web/src/TranscriptPanel.tsx`, `web/src/style.css`

- **Call page:** the label الموظف appears in blue and العميل in green before each line. Clicking a line plays the
  audio from there.
- **.txt download:**

```
[00:00] الموظف: السلام عليكم معك سارة
[00:02] العميل: ابي الفاتورة
```

- **Numbers line:** admin and supervisor only. The server leaves the numbers out for agents.

## Step 10: KPI page

**Files:** `callsight/kpi.py` → `_talk()`, `web/src/KpiPage.tsx`

A new box shows team and per-agent averages, labelled "آلي — للعرض فقط". It is display only and is never part of
a score or the PDF. Calls marked "unsure" are not counted.

## Step 11: give the older calls speakers

**Files:** `callsight/asr/queueing.py` → `resplit()`, `callsight/cli.py`

Old calls have no word times, so they are transcribed once more:

```
python -m callsight transcribe --resplit --limit 500
```

- The 500 rating-set calls go first, then the newest calls.
- If a window's text comes out different, the old text is kept for that window.

## Step 12: the GPU server

**Files:** `docker/asr-worker/requirements-diarize.txt`, `docker/asr-worker/Dockerfile`,
`.github/workflows/gpu-setup.yml` (task `build-diarize`), `deploy/aws/compose.gpu.yml`

1. `pyannote.audio==3.3.2` and all of its parts are pinned to exact versions, so the Whisper libraries do not change.
2. The 3 models are pinned to exact versions (commit IDs) and downloaded once.
3. Your Hugging Face token is used only during that download. It is sent through stdin, so it never appears in a
   file, a command or a log. The internet is switched off again afterwards.
4. `DIARIZATION=off` switches the whole feature off.

## Step 13: tests

**Files:** `tests/test_diarization.py` (18 tests, no GPU needed), `tests/test_diarization_db.py` (11 tests,
database)

The tests cover the word gluing, the overlap, the speaker lines, the naming rules, the numbers, saving, what agents
may see, the .txt file, the KPI averages, the re-run and the migration.

---

# Doing the same in another project

## What you need

| Need | Why | How |
|---|---|---|
| A GPU (about 4 GB free besides Whisper) | pyannote is slow on a CPU | the same GPU that runs Whisper |
| A free Hugging Face account and token | the pyannote models are "gated" | huggingface.co → accept the terms of `pyannote/speaker-diarization-3.1` **and** `pyannote/segmentation-3.0` → Settings → Access Tokens → "Read" |
| Python packages | | `pyannote.audio==3.3.2`, `torch`, `torchaudio` (same version as torch), `transformers` |
| Audio | | mono 16 kHz WAV is best |
| Speech-to-text **with word times** | to give each word a speaker | Whisper (as here), or faster-whisper / whisperX, which give word times directly |
| Your own rules | to know which voice is "the agent" | your company's greeting words and name, in your language |

## The smallest working version (for a new project)

This is a **short sketch** for a fresh project. The CallSight code above is the tested, safer version: same text,
offline, privacy rules. Use the sketch to start, then add what you need.

```python
# pip install pyannote.audio==3.3.2 transformers torch torchaudio
import torch
from pyannote.audio import Pipeline
from transformers import pipeline

AUDIO = "call.wav"            # mono, 16 kHz
TOKEN = "hf_..."              # your Hugging Face token: keep it in an env variable, never in code you share

# 1) text with word times
asr = pipeline("automatic-speech-recognition", model="openai/whisper-large-v3",
               torch_dtype=torch.float16, device="cuda")
out = asr(AUDIO, return_timestamps="word", generate_kwargs={"language": "ar"})
words = [(c["text"].strip(), c["timestamp"][0], c["timestamp"][1]) for c in out["chunks"]]

# 2) who speaks when
dia = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=TOKEN).to(torch.device("cuda"))
turns = [(t.start, t.end, spk) for t, _, spk in dia(AUDIO, num_speakers=2).itertracks(yield_label=True)]

# 3) each word -> the speaker with the most overlap; words in a row -> one line
def speaker_of(s, e):
    best = max(turns, key=lambda t: min(e, t[1]) - max(s, t[0]))
    return best[2]

lines = []
for w, s, e in words:
    sp = speaker_of(s, e or s + 0.3)
    if lines and lines[-1][0] == sp:
        lines[-1][1].append(w)
    else:
        lines.append([sp, [w]])

# 4) who is the agent: the first speaker if they greet
GREETINGS = ("السلام عليكم", "معك", "حياك")
first = lines[0][0]
agent = first if any(g in " ".join(lines[0][1]) for g in GREETINGS) else None

for sp, ws in lines:
    who = "unknown" if agent is None else ("agent" if sp == agent else "customer")
    print(f"{who}: {' '.join(ws)}")
```

## Checklist for the new project

1. Make the Hugging Face account, accept the terms of both models, and make the token.
2. Install the packages and pin exact versions after the first install that works (`pip freeze`).
3. Run the sketch on 5 real calls and read the result.
4. Write your greeting word list, then check 30 calls by hand (who speaks first) and aim for at least 27 of 30.
5. Decide who may see the numbers (talk %, interruptions).
6. Privacy: the text lines are call content. Delete them together with the call, and never write them into logs.
7. For a server without internet, download the models once (pinned versions), then load them from disk only.
