"""Settings from .env (never hard-coded) + the team config file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .ingest.classify import TeamConfig

ROOT = Path(__file__).resolve().parent.parent
RIYADH = timezone(timedelta(hours=3), "AST")   # Saudi Arabia: UTC+3, no daylight saving


@dataclass(frozen=True)
class Settings:
    yeastar_base_url: str
    yeastar_client_id: str
    yeastar_client_secret: str
    yeastar_verify_ssl: bool
    database_url: str
    redis_url: str
    audio_dir: Path
    teams_file: Path

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        missing = [k for k in ("YEASTAR_BASE_URL", "YEASTAR_CLIENT_ID", "YEASTAR_CLIENT_SECRET", "DATABASE_URL")
                   if not os.getenv(k)]
        if missing:
            raise RuntimeError(f"Missing in .env: {', '.join(missing)} (see .env.example)")
        return cls(
            yeastar_base_url=os.environ["YEASTAR_BASE_URL"],
            yeastar_client_id=os.environ["YEASTAR_CLIENT_ID"],
            yeastar_client_secret=os.environ["YEASTAR_CLIENT_SECRET"],
            yeastar_verify_ssl=os.getenv("YEASTAR_VERIFY_SSL", "true").lower() not in ("0", "false", "no"),
            database_url=os.environ["DATABASE_URL"],
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            audio_dir=Path(os.getenv("AUDIO_DIR", str(ROOT / "data" / "audio"))),
            teams_file=Path(os.getenv("TEAMS_FILE", str(ROOT / "config" / "teams.yaml"))),
        )


def load_teams(path: Path) -> TeamConfig:
    """config/teams.yaml -> TeamConfig. Format: teams: {<name>: {extensions: [..], queues: [..]}}"""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    ext_team: dict[str, str] = {}
    queue_team: dict[str, str] = {}
    for name, spec in (data.get("teams") or {}).items():
        for ext in (spec or {}).get("extensions") or []:
            ext_team[str(ext)] = name
        for q in (spec or {}).get("queues") or []:
            queue_team[str(q)] = name
    if not ext_team:
        raise RuntimeError(f"{path}: no team extensions configured")
    return TeamConfig(ext_team=ext_team, queue_team=queue_team)
