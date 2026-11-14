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
    database_url: str
    redis_url: str
    audio_dir: Path
    teams_file: Path
    # Only the PBX adapter needs these (checked in app.pbx()), so the asr worker runs without the PBX secret.
    yeastar_base_url: str = ""
    yeastar_client_id: str = ""
    yeastar_client_secret: str = ""
    yeastar_verify_ssl: bool = True

    def require_pbx(self) -> None:
        missing = [k for k, v in (("YEASTAR_BASE_URL", self.yeastar_base_url),
                                  ("YEASTAR_CLIENT_ID", self.yeastar_client_id),
                                  ("YEASTAR_CLIENT_SECRET", self.yeastar_client_secret)) if not v]
        if missing:
            raise RuntimeError(f"Missing in .env: {', '.join(missing)} (see .env.example)")
    yeastar_ca_file: str | None = None          # PBX's own certificate (PEM); verified against it when set
    yeastar_cert_sha256: str | None = None      # fingerprint pin, checked before each login
    yeastar_check_hostname: bool = True
    backup_dir: Path = ROOT / "data" / "backup"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        if not os.getenv("DATABASE_URL"):
            raise RuntimeError("Missing in .env: DATABASE_URL (see .env.example)")
        return cls(
            yeastar_base_url=os.getenv("YEASTAR_BASE_URL", ""),
            yeastar_client_id=os.getenv("YEASTAR_CLIENT_ID", ""),
            yeastar_client_secret=os.getenv("YEASTAR_CLIENT_SECRET", ""),
            yeastar_verify_ssl=os.getenv("YEASTAR_VERIFY_SSL", "true").lower() not in ("0", "false", "no"),
            database_url=os.environ["DATABASE_URL"],
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            audio_dir=Path(os.getenv("AUDIO_DIR", str(ROOT / "data" / "audio"))),
            teams_file=Path(os.getenv("TEAMS_FILE", str(ROOT / "config" / "teams.yaml"))),
            yeastar_ca_file=os.getenv("YEASTAR_CA_FILE") or None,
            yeastar_cert_sha256=os.getenv("YEASTAR_CERT_SHA256") or None,
            yeastar_check_hostname=os.getenv("YEASTAR_TLS_CHECK_HOSTNAME", "true").lower() not in ("0", "false", "no"),
            backup_dir=Path(os.getenv("BACKUP_DIR", str(ROOT / "data" / "backup"))),
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
