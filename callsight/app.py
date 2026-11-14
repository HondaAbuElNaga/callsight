"""Shared wiring for the CLI and the Celery worker: settings, DB sessions, one PBX adapter per process."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from .config import ROOT, Settings, load_teams
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


def reports_dir() -> Path:
    """Where scheduled PDF reports are archived (REPORTS_DIR; /data/reports in Docker)."""
    return Path(os.getenv("REPORTS_DIR", str(ROOT / "data" / "reports")))


def redis_client(timeout: float = 2.0):
    import redis
    return redis.Redis.from_url(settings().redis_url, socket_timeout=timeout, socket_connect_timeout=timeout)


def auth_guard(who: str, fail_closed: bool):
    """The shared pbx:auth_blocked switch (spec live-board, decision 1)."""
    from .pbx.authblock import AuthGuard
    return AuthGuard(redis_client(), who, fail_closed=fail_closed)


def pbx_adapter(who: str, fail_closed: bool = False) -> YeastarAdapter:
    from .pbx.tls import verify_setting
    s = settings()
    s.require_pbx()
    verify = verify_setting(s.yeastar_ca_file, s.yeastar_verify_ssl, s.yeastar_check_hostname,
                            cert_sha256=s.yeastar_cert_sha256)
    return YeastarAdapter(s.yeastar_base_url, s.yeastar_client_id, s.yeastar_client_secret,
                          verify_ssl=verify, cert_sha256=s.yeastar_cert_sha256,
                          guard=auth_guard(who, fail_closed))


@lru_cache(maxsize=1)
def pbx() -> YeastarAdapter:
    """One adapter per process: the token is reused and the failed-login counter survives between jobs.
    Checks pbx:auth_blocked before every login; Redis unreachable (e.g. the CLI on the PC, where Redis is not
    published) = log a warning and rely on the in-process failed-login limit."""
    import sys
    return pbx_adapter("cli" if "celery" not in sys.argv[0] else "worker")


def backup_config():
    from .backup import BackupConfig
    s = settings()
    return BackupConfig(database_url=s.database_url, backup_dir=s.backup_dir, audio_dir=s.audio_dir,
                        reports_dir=reports_dir())


def retention_config():
    from .retention import RetentionConfig
    return RetentionConfig.load(Path(os.getenv("RETENTION_FILE", str(ROOT / "config" / "retention.yaml"))))
