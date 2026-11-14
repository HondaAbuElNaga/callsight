"""Settings of the analysis step: .env (LLM_*, OLLAMA_URL, NLP_*) + config/topics.yaml + config/rules.yaml.
Both YAML files are re-read on use, so edits apply without a restart."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from ..config import ROOT

# Prompt and transcript never leave this PC (requirement 2): the worker talks to the Ollama container only.
ALLOWED_OLLAMA_HOSTS = ("ollama", "localhost", "127.0.0.1")
TOPIC_KEY = re.compile(r"^[a-z_]{1,30}$")
OTHER = "other"


class ConfigError(ValueError):
    pass


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes")


def check_ollama_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in ALLOWED_OLLAMA_HOSTS:
        raise ConfigError(f"OLLAMA_URL must be http://ollama:11434 (or localhost) - got host {parts.hostname!r}")
    return url.rstrip("/")


@dataclass(frozen=True)
class LlmConfig:
    model: str = "qwen2.5:7b"
    ollama_url: str = "http://ollama:11434"
    num_ctx: int = 8192
    timeout_s: float = 300.0
    max_chars: int = 12_000          # transcript budget inside num_ctx; longer calls are head+tail trimmed
    enabled: bool = False            # NLP_ENABLED: automatic analysis of new transcripts (off until the llm profile runs)
    batch: int = 20
    topics_file: Path = ROOT / "config" / "topics.yaml"
    rules_file: Path = ROOT / "config" / "rules.yaml"
    think: bool | None = None        # qwen3: False (no thinking); qwen2.5: not sent

    @classmethod
    def from_env(cls) -> "LlmConfig":
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        model = os.getenv("LLM_MODEL", "qwen2.5:7b").strip()
        return cls(model=model,
                   ollama_url=check_ollama_url(os.getenv("OLLAMA_URL", "http://ollama:11434").strip()),
                   num_ctx=int(os.getenv("LLM_NUM_CTX", "8192")),
                   timeout_s=float(os.getenv("LLM_TIMEOUT_S", "300")),
                   max_chars=int(os.getenv("LLM_MAX_CHARS", "12000")),
                   enabled=_truthy(os.getenv("NLP_ENABLED")),
                   batch=int(os.getenv("NLP_BATCH", "20")),
                   topics_file=Path(os.getenv("TOPICS_FILE", str(ROOT / "config" / "topics.yaml"))),
                   rules_file=Path(os.getenv("RULES_FILE", str(ROOT / "config" / "rules.yaml"))),
                   think=False if model.startswith("qwen3") else None)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def load_topics(path: Path) -> dict[str, str]:
    """key -> Arabic label, in file order. 'other' must exist; keys are stable ids (a-z, _)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    topics = {str(k): str(v).strip() for k, v in (data.get("topics") or {}).items()}
    bad = [k for k in topics if not TOPIC_KEY.match(k)]
    if bad or not topics or OTHER not in topics or not all(topics.values()):
        raise ConfigError(f"{path.name}: topics need keys a-z/_ with a label, and '{OTHER}' must be present")
    return topics


@dataclass(frozen=True)
class SilenceRules:
    threshold_dbfs: float = -40.0
    frame_ms: int = 50
    min_silence_s: float = 2.0
    long_silence_s: float = 10.0

    @property
    def version(self) -> str:
        return f"rms{self.threshold_dbfs:g}f{self.frame_ms}m{self.min_silence_s:g}l{self.long_silence_s:g}"


@dataclass(frozen=True)
class Rules:
    flags: dict[str, tuple[str, ...]] = field(default_factory=dict)   # kind -> phrases (as written)
    silence: SilenceRules = SilenceRules()
    max_dropped_share: float = 0.30
    version: str = "r0"


FLAG_KINDS = ("bad_words", "escalation", "competitor")


def load_rules(path: Path) -> Rules:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("flags") or {}
    unknown = set(raw) - set(FLAG_KINDS)
    if unknown:
        raise ConfigError(f"{path.name}: unknown flag kinds {sorted(unknown)}; use {FLAG_KINDS}")
    flags = {k: tuple(str(p).strip() for p in (raw.get(k) or []) if str(p).strip()) for k in FLAG_KINDS}
    if any(len(p) > 100 for ps in flags.values() for p in ps):
        raise ConfigError(f"{path.name}: a phrase is longer than 100 characters")
    s = data.get("silence") or {}
    silence = SilenceRules(threshold_dbfs=float(s.get("threshold_dbfs", -40)), frame_ms=int(s.get("frame_ms", 50)),
                           min_silence_s=float(s.get("min_silence_s", 2)),
                           long_silence_s=float(s.get("long_silence_s", 10)))
    if not (-90 <= silence.threshold_dbfs < 0 and 10 <= silence.frame_ms <= 1000
            and 0 < silence.min_silence_s <= silence.long_silence_s):
        raise ConfigError(f"{path.name}: silence settings out of range")
    share = float((data.get("analysis") or {}).get("max_dropped_share", 0.30))
    if not 0 <= share <= 1:
        raise ConfigError(f"{path.name}: analysis.max_dropped_share must be 0..1")
    return Rules(flags=flags, silence=silence, max_dropped_share=share, version="r-" + file_hash(path))


def rating_excluded(path: Path | None = None) -> frozenset[int]:
    """Call ids kept out of the satisfaction rating (queue, blind set, training): config/rating-exclude.yaml.
    For test calls made while the system was being set up. A missing or empty file excludes nothing."""
    path = path or Path(os.getenv("RATING_EXCLUDE_FILE", str(ROOT / "config" / "rating-exclude.yaml")))
    if not path.exists():
        return frozenset()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    ids = data.get("call_ids") or []
    if not isinstance(ids, list) or any(not isinstance(i, int) or isinstance(i, bool) for i in ids):
        raise ConfigError(f"{path.name}: call_ids must be a list of call numbers")
    return frozenset(ids)
