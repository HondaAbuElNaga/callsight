"""PBX adapter interface. The app only talks to this; one subclass per PBX brand."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path


class PbxError(RuntimeError):
    """Any PBX API failure."""


class PbxUnavailable(PbxError):
    """The whole PBX can't be used right now (down, login refused, token limit).
    Jobs stop the batch on this instead of blaming single calls/recordings."""


class AuthError(PbxUnavailable):
    """Login refused by the PBX."""


class TokenLimit(PbxUnavailable):
    """PBX refuses new tokens because too many are open (Yeastar: max 8). Not a credentials problem."""


class AuthBlocked(AuthError):
    """Too many failed logins in a row; we stop trying so the PBX doesn't block our IP."""


class PbxAdapter(ABC):
    @abstractmethod
    def fetch_cdr_since(self, last_id: int, overlap: int = 300) -> list[dict]:
        """CDR rows with id > last_id - overlap (newest PBX rows), as raw dicts."""

    @abstractmethod
    def fetch_cdr_between(self, start: datetime, end: datetime) -> list[dict]:
        """CDR rows with start <= timestamp < end (timezone-aware datetimes)."""

    @abstractmethod
    def download_recording(self, file: str, dest: Path) -> Path:
        """Download a recording by its CDR `record_file` name to `dest`."""

    @abstractmethod
    def list_queues(self) -> list[dict]:
        """[{number, name, agents: [{ext, name}]}]"""

    @abstractmethod
    def list_extensions(self) -> list[dict]:
        """[{ext, name}]"""

    def close(self) -> None:  # pragma: no cover - optional
        pass
