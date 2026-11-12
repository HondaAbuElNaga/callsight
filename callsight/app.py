"""Shared wiring for the CLI and the Celery worker: settings, DB sessions, one PBX adapter per process."""
from __future__ import annotations

from functools import lru_cache

from .config import Settings, load_teams
from .db.session import make_session_factory
from .ingest.classify import TeamConfig
from .pbx.yeastar import YeastarAdapter


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings.from_env()


@lru_cache(maxsize=1)
def session_factory():
    return make_session_factory(settings().database_url)


def teams() -> TeamConfig:
    return load_teams(settings().teams_file)   # re-read each time, so edits apply without restart


@lru_cache(maxsize=1)
def pbx() -> YeastarAdapter:
    """One adapter per process: the token is reused and the failed-login counter survives between jobs."""
    s = settings()
    return YeastarAdapter(s.yeastar_base_url, s.yeastar_client_id, s.yeastar_client_secret,
                          verify_ssl=s.yeastar_verify_ssl)
