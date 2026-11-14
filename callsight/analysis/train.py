"""Fine-tune a dialect Arabic BERT as a 1-10 customer-satisfaction regressor (spec 2026-11-14-call-analysis).

Runs in the asr-worker image (exact-pinned torch + transformers), offline (`HF_HUB_OFFLINE=1`, enforced: the
real run refuses to start without it). The report and the tuned model are written to --out: give it a writable
host folder, a `run --rm` container's own files are gone when it exits (data/ is not in git):
    docker compose --profile asr run --rm -v ./data/train:/out asr-worker \
        python -m callsight.analysis.train --out /out
Base model (decision 1): CAMeL-Lab/bert-base-arabic-camelbert-da (Apache-2.0) at a pinned revision; download it
once into the hf-models volume with the asr-bench-download container. Only models in ALLOWED_BASE_MODELS run:
MARBERT is NOT allowed (research-only licence; commercial use needs the authors' written permission).

Labels: the target is 500 rated calls = at least 400 training calls (not in the blind set) + the 100 blind-set
calls (the test set).

Data: one example per labelled call = kept transcript windows of the displayed transcript (whole call: no
diarization yet), target = mean of the human scores. Blind-set calls are never trained on (decision 8); the test is
the blind set only, labels given without pre-fill, compared with the LLM estimate on the same calls (only the
estimate of the configured LLM_MODEL under the current prompt version: never a mix of models or prompts).
BERT reads 512 tokens: longer calls keep the first 128 and the last 382 tokens (head+tail truncation).
The report holds numbers only (no transcript text).

Self-test with a tiny random model + synthetic data (no download, no DB):
    python -m callsight.analysis.train --selftest
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_MODEL = "CAMeL-Lab/bert-base-arabic-camelbert-da"
BASE_MODEL = os.getenv("SAT_BASE_MODEL", DEFAULT_BASE_MODEL).strip()
BASE_REVISION = os.getenv("SAT_BASE_REVISION", "231698eab9ebf0ae7b518a64277b81b2fe829f2d").strip()
ALLOWED_BASE_MODELS = (DEFAULT_BASE_MODEL,)      # licence checked (Apache-2.0); add a model only after checking its licence
MAX_TOKENS = 512
HEAD_TOKENS = 128
MIN_TRAIN = 400                     # Phase 3: 500 rated calls = >= 400 training calls + the 100 blind test calls
DONE_WHEN = (2.0, 0.80)             # within +-2 on >= 80 % of the blind test set


@dataclass
class Example:
    call_id: int
    text: str
    score: float                    # mean human score 1-10
    llm_score: int | None = None    # LLM baseline (blind calls: computed but never shown)


# ---------- data (DB; no torch) ----------

def check_base_model(name: str) -> None:
    """Refuse a base model whose licence was not checked (decision 1)."""
    if name in ALLOWED_BASE_MODELS:
        return
    if "marbert" in name.lower():
        raise ValueError(f"base model {name!r} is not allowed: MARBERT is research-only "
                         "(commercial use needs the authors' written permission)")
    raise ValueError(f"base model {name!r} is not in the allowed list {ALLOWED_BASE_MODELS}: "
                     "check its licence first, then add it to ALLOWED_BASE_MODELS")


def load_examples(session, asr_model: str, llm_model: str | None = None, prompt_version: str | None = None
                  ) -> tuple[list[Example], list[Example]]:
    """(train, test). Train = labelled calls NOT in the blind set. Test = blind-set calls with labels given
    without any pre-fill. A call is in exactly one of them.
    LLM baseline = the done analysis of exactly `llm_model` + `prompt_version` (None when the call has none)."""
    from sqlalchemy import func, select

    from ..db.models import BlindSet, CallAnalysis, SatisfactionLabel, TranscriptSegment
    from ..transcripts import displayed

    blind = set(session.scalars(select(BlindSet.call_id)))
    rows = session.execute(select(SatisfactionLabel.call_id, func.avg(SatisfactionLabel.score),
                                  func.bool_and(SatisfactionLabel.prefill.is_(None)))
                           .group_by(SatisfactionLabel.call_id).order_by(SatisfactionLabel.call_id)).all()
    train, test = [], []
    for call_id, mean, no_prefill in rows:
        if call_id in blind and not no_prefill:
            continue                               # a pre-filled label can't be part of the blind evaluation
        t = displayed(session, call_id, asr_model)
        if t is None or t.status != "done":
            continue
        segs = session.scalars(select(TranscriptSegment.text).where(
            TranscriptSegment.transcript_id == t.id, TranscriptSegment.flag.is_(None))
            .order_by(TranscriptSegment.seq)).all()
        text = " ".join(" ".join((x or "").split()) for x in segs if (x or "").strip())
        if not text:
            continue
        llm_score = None
        if llm_model and prompt_version:
            llm_score = session.scalar(select(CallAnalysis.llm_score).where(
                CallAnalysis.call_id == call_id, CallAnalysis.model == llm_model,
                CallAnalysis.prompt_version == prompt_version, CallAnalysis.status == "done").limit(1))
        ex = Example(call_id, text, float(mean), llm_score)
        (test if call_id in blind else train).append(ex)
    return train, test


def head_tail(ids: list[int], max_len: int = MAX_TOKENS, head: int = HEAD_TOKENS) -> list[int]:
    """Token ids without [CLS]/[SEP] -> at most max_len - 2 ids: first `head` + the rest from the end."""
    room = max_len - 2
    if len(ids) <= room:
        return ids
    return ids[:head] + ids[len(ids) - (room - head):]


def evaluate(pred: list[float], gold: list[float]) -> dict:
    n = len(gold)
    if not n:
        return {"n": 0, "mae": None, "within2": None}
    err = [abs(p - g) for p, g in zip(pred, gold)]
    return {"n": n, "mae": round(sum(err) / n, 3), "within2": round(sum(e <= DONE_WHEN[0] for e in err) / n, 3)}


# ---------- training (torch + transformers, asr-worker image only) ----------

def _encode(tok, texts: list[str]):
    import torch
    rows = []
    for text in texts:
        ids = head_tail(tok(text, add_special_tokens=False)["input_ids"])
        rows.append([tok.cls_token_id] + ids + [tok.sep_token_id])
    width = max(len(r) for r in rows)
    ids = torch.full((len(rows), width), tok.pad_token_id, dtype=torch.long)
    mask = torch.zeros((len(rows), width), dtype=torch.long)
    for i, r in enumerate(rows):
        ids[i, :len(r)] = torch.tensor(r)
        mask[i, :len(r)] = 1
    return ids, mask


def train(train_ex: list[Example], test_ex: list[Example], model_dir: str, out_dir: Path, *, revision: str | None = None,
          epochs: int = 3, lr: float = 2e-5, batch: int = 8, seed: int = 1, min_train: int = MIN_TRAIN,
          llm_model: str | None = None, prompt_version: str | None = None) -> dict:
    if len(train_ex) < min_train:
        raise ValueError(f"only {len(train_ex)} labelled training calls; need at least {min_train}")
    if not test_ex:
        raise ValueError("no rated blind-set calls to test on")
    if set(e.call_id for e in train_ex) & set(e.call_id for e in test_ex):
        raise ValueError("a blind-set call is in the training data")
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    random.seed(seed)
    torch.manual_seed(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(model_dir, revision=revision)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir, revision=revision, num_labels=1,
                                                               problem_type="regression").to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr)
    scale = lambda s: (s - 5.5) / 4.5                                  # noqa: E731  1..10 -> -1..1
    unscale = lambda y: min(10.0, max(1.0, y * 4.5 + 5.5))             # noqa: E731
    model.train()
    for _ in range(epochs):
        order = list(range(len(train_ex)))
        random.shuffle(order)
        for i in range(0, len(order), batch):
            part = [train_ex[j] for j in order[i:i + batch]]
            ids, mask = _encode(tok, [e.text for e in part])
            target = torch.tensor([[scale(e.score)] for e in part], dtype=torch.float)
            out = model(input_ids=ids.to(device), attention_mask=mask.to(device), labels=target.to(device))
            out.loss.backward()
            opt.step()
            opt.zero_grad()
    model.eval()
    preds = []
    with torch.no_grad():
        for i in range(0, len(test_ex), batch):
            ids, mask = _encode(tok, [e.text for e in test_ex[i:i + batch]])
            logits = model(input_ids=ids.to(device), attention_mask=mask.to(device)).logits
            preds += [unscale(float(v)) for v in logits.squeeze(-1).cpu().tolist()]
    gold = [e.score for e in test_ex]
    with_llm = [(e.llm_score, e.score) for e in test_ex if e.llm_score is not None]
    report = {
        "base_model": model_dir, "revision": revision, "train_calls": len(train_ex), "test_calls": len(test_ex),
        "epochs": epochs, "lr": lr, "seed": seed, "device": device,
        "tuned": evaluate(preds, gold),
        "llm_baseline": evaluate([float(p) for p, _ in with_llm], [g for _, g in with_llm]),
        "llm_model": llm_model, "llm_prompt_version": prompt_version,
        "done_when": f"within +-{DONE_WHEN[0]:g} on >= {DONE_WHEN[1]:.0%} of the blind test set",
    }
    w2 = report["tuned"]["within2"]
    report["done_when_met"] = bool(w2 is not None and w2 >= DONE_WHEN[1] and len(test_ex) >= 100)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(_markdown(report), encoding="utf-8")
    model.save_pretrained(out_dir / "model")
    tok.save_pretrained(out_dir / "model")
    return report


def _markdown(r: dict) -> str:
    def row(name, m):
        return f"| {name} | {m['n']} | {m['mae']} | {m['within2']} |"
    return "\n".join([
        "# Satisfaction model: tuned BERT vs LLM estimate (blind test set)", "",
        f"Base: `{r['base_model']}` @ `{r['revision']}`; {r['train_calls']} training calls, "
        f"{r['test_calls']} blind test calls; {r['epochs']} epochs, lr {r['lr']}, seed {r['seed']}.", "",
        "| model | n | MAE | share within ±2 |", "|---|---|---|---|",
        row("tuned BERT", r["tuned"]), row("LLM first estimate", r["llm_baseline"]), "",
        f"LLM baseline: model `{r.get('llm_model')}`, prompt version `{r.get('llm_prompt_version')}` "
        "(only calls analysed with exactly this model and prompt).", "",
        f"Phase 3 'Done when' ({r['done_when']}): {'MET' if r['done_when_met'] else 'NOT met'}.", ""])


# ---------- self-test (tiny random model, synthetic data) ----------

GOOD = ["شكرا", "ممتاز", "تمام", "الله", "يعطيك", "العافية", "خدمة", "حلوة"]
BAD = ["زعلان", "سيء", "مشكلة", "ما", "انحلت", "بشتكي", "تأخير", "المدير"]


def _tiny_model(path: Path) -> str:
    from transformers import BertConfig, BertForSequenceClassification, BertTokenizer
    path.mkdir(parents=True, exist_ok=True)
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"] + GOOD + BAD + ["و", "يا", "اخي"]
    (path / "vocab.txt").write_text("\n".join(vocab) + "\n", encoding="utf-8")
    tok = BertTokenizer(str(path / "vocab.txt"), do_lower_case=False, tokenize_chinese_chars=False)
    cfg = BertConfig(vocab_size=len(vocab), hidden_size=32, num_hidden_layers=2, num_attention_heads=2,
                     intermediate_size=64, max_position_embeddings=MAX_TOKENS, num_labels=1,
                     problem_type="regression")
    BertForSequenceClassification(cfg).save_pretrained(path)
    tok.save_pretrained(path)
    return str(path)


def synthetic(n: int, seed: int, start_id: int = 1) -> list[Example]:
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        score = rnd.randint(1, 10)
        words = [rnd.choice(GOOD if rnd.random() < score / 10 else BAD) for _ in range(rnd.randint(5, 700))]
        out.append(Example(start_id + i, " ".join(words), float(score), llm_score=max(1, min(10, score + 3))))
    return out


def selftest(work: Path) -> dict:
    model_dir = _tiny_model(work / "tiny")
    report = train(synthetic(60, 1), synthetic(20, 2, start_id=1000), model_dir, work / "out", epochs=8, lr=3e-3,
                   min_train=10)
    assert report["tuned"]["n"] == 20 and math.isfinite(report["tuned"]["mae"])
    assert report["llm_baseline"]["n"] == 20 and 0 < report["llm_baseline"]["mae"] <= 3.0
    assert (work / "out" / "report.md").is_file() and (work / "out" / "model").is_dir()
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m callsight.analysis.train", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--selftest", action="store_true", help="tiny random model + synthetic data (no DB, no download)")
    p.add_argument("--out", default="/out", help="writable folder for report.json, report.md and the model "
                                                   "(mount a host folder: -v ./data/train:/out)")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--lr", type=float, default=2e-5)
    a = p.parse_args(argv)
    if a.selftest:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            r = selftest(Path(tmp))
        print(f"selftest OK: tuned MAE {r['tuned']['mae']}, within2 {r['tuned']['within2']}; "
              f"LLM baseline MAE {r['llm_baseline']['mae']}")
        return 0
    if os.getenv("HF_HUB_OFFLINE") != "1":
        print("ERROR: set HF_HUB_OFFLINE=1 (training must not contact huggingface.co; run it in the asr-worker "
              "container, see the command at the top of this file)", file=sys.stderr)
        return 1
    try:
        check_base_model(BASE_MODEL)
    except ValueError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    from .. import app as wiring
    from . import llm
    from .settings import LlmConfig, load_topics
    cfg = LlmConfig.from_env()
    pv = llm.prompt_version(load_topics(cfg.topics_file))
    with wiring.session_factory()() as s:
        tr, te = load_examples(s, os.getenv("ASR_MODEL", "whisper-large-v3").strip(), cfg.model, pv)
    try:
        r = train(tr, te, BASE_MODEL, Path(a.out), revision=BASE_REVISION, epochs=a.epochs, lr=a.lr,
                  llm_model=cfg.model, prompt_version=pv)
    except ValueError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    print(_markdown(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
