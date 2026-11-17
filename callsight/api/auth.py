"""Passwords (Argon2), login token (signed JWT in an HttpOnly cookie), lockout after wrong passwords."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import string
from collections.abc import Collection
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ApiClient, AppUser

COOKIE_NAME = "cs_session"
TOKEN_HOURS = 8
MAX_FAILED = 5
LOCK_MINUTES = 15
MIN_PASSWORD = 10
ROLES = ("admin", "supervisor", "agent")
STAFF_ROLES = ("admin", "supervisor")          # see every team call; agents see only their own (calls.visible_scope)

_ph = PasswordHasher()
_DUMMY_HASH = _ph.hash("timing-equaliser-not-a-password")


LOGIN_FAILED = "wrong username or password, or account locked for 15 minutes after 5 wrong tries"


class LoginError(Exception):
    """One message for every failure, so the answer never tells which usernames exist."""
    def __init__(self):
        super().__init__(LOGIN_FAILED)


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    return _ph.hash(password)


def generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _verify(hash_: str, password: str) -> bool:
    try:
        return _ph.verify(hash_, password)
    except (VerificationError, InvalidHashError):
        return False


def authenticate(session: Session, username: str, password: str, now: datetime | None = None) -> AppUser:
    """Returns the user or raises LoginError (same message and similar time for every failure)."""
    now = now or datetime.now(timezone.utc)
    # Row lock: parallel wrong-password requests are counted one after the other (no lost updates).
    user = session.scalars(select(AppUser).where(AppUser.username == username.strip().lower())
                           .with_for_update()).first()
    if user is None or not user.active:
        _verify(_DUMMY_HASH, password)          # same time as a real check
        session.rollback()
        raise LoginError()
    if user.locked_until and user.locked_until > now:
        _verify(_DUMMY_HASH, password)
        session.rollback()
        raise LoginError()
    if not _verify(user.password_hash, password):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until, user.failed_logins = now + timedelta(minutes=LOCK_MINUTES), 0
        session.commit()
        raise LoginError()
    user.failed_logins, user.locked_until = 0, None
    if _ph.check_needs_rehash(user.password_hash):
        user.password_hash = _ph.hash(password)
    session.commit()
    return user


def make_token(user: AppUser, secret: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    payload = {"sub": str(user.id), "ver": user.token_version, "iat": now, "exp": now + timedelta(hours=TOKEN_HOURS)}
    return jwt.encode(payload, secret, algorithm="HS256")


def user_from_token(session: Session, token: str | None, secret: str) -> AppUser | None:
    if not token:
        return None
    try:
        data = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
        user = session.get(AppUser, int(data["sub"]))
    except (jwt.PyJWTError, ValueError, KeyError):
        return None
    if user is None or not user.active or user.token_version != data.get("ver"):
        return None
    return user


# ---------- API keys for other systems (spec 2026-11-18-erp-api) ----------

API_KEY_HEADER = "X-Api-Key"
ACT_AS_HEADER = "X-Act-As"
KEY_USED_EVERY = timedelta(minutes=1)        # last_used_at is written at most once a minute per key


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_api_key() -> tuple[str, str, str]:
    """(key shown once, prefix kept to find the row, SHA-256 kept to check the key). Format: cs_<10>_<48 hex>."""
    alphabet = string.ascii_letters + string.digits
    prefix = "".join(secrets.choice(alphabet) for _ in range(10))
    key = f"cs_{prefix}_{secrets.token_hex(24)}"
    return key, prefix, _key_hash(key)


def _key_prefix(key: str) -> str | None:
    parts = key.split("_")
    return parts[1] if len(parts) == 3 and parts[0] == "cs" and len(parts[1]) == 10 and parts[1].isalnum() else None


def user_from_api_key(session: Session, key: str | None, act_as: str | None,
                      now: datetime | None = None) -> AppUser | None:
    """The CallSight user a system request acts for, or None (bad/disabled key, unknown or disabled user).
    The answer gets exactly that user's role and scope, and every audit row names that user."""
    key, act_as = (key or "").strip(), (act_as or "").strip().lower()
    prefix = _key_prefix(key)
    if prefix is None or not act_as or len(act_as) > 50:
        return None
    client = session.scalars(select(ApiClient).where(ApiClient.key_prefix == prefix)).first()
    if client is None or not client.active or not hmac.compare_digest(client.key_hash, _key_hash(key)):
        return None
    user = session.scalars(select(AppUser).where(AppUser.username == act_as)).first()
    if user is None or not user.active:
        return None
    now = now or datetime.now(timezone.utc)
    if client.last_used_at is None or now - client.last_used_at > KEY_USED_EVERY:
        client.last_used_at = now
        session.commit()
    return user


# ---------- account management (CLI) ----------

def _check_agent_ext(role: str, ext: str | None, team_exts: Collection[str] | None) -> None:
    """An agent account must name its own extension, and it must be a team extension in teams.yaml."""
    if role != "agent":
        return
    if not ext:
        raise ValueError("an agent account needs --ext (the agent's own extension)")
    if team_exts is None or ext not in team_exts:
        raise ValueError(f"extension {ext!r} is not a team extension in teams.yaml")


def create_user(session: Session, username: str, password: str, role: str, ext: str | None = None,
                team_exts: Collection[str] | None = None) -> AppUser:
    username = username.strip().lower()
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    _check_agent_ext(role, ext, team_exts)
    if not username or not username.replace("_", "").replace(".", "").isalnum() or len(username) > 50:
        raise ValueError("username: letters, digits, '.', '_' only (max 50)")
    if session.scalars(select(AppUser).where(AppUser.username == username)).first():
        raise ValueError(f"user {username!r} already exists")
    user = AppUser(username=username, password_hash=hash_password(password), role=role, ext=ext, active=True,
                   token_version=0, failed_logins=0)
    session.add(user)
    session.commit()
    return user


def set_password(session: Session, username: str, password: str) -> None:
    user = _get(session, username)
    user.password_hash = hash_password(password)
    user.token_version += 1
    user.failed_logins, user.locked_until = 0, None
    session.commit()


def set_ext(session: Session, username: str, ext: str | None, team_exts: Collection[str] | None = None) -> None:
    """New extension for a user. Ends that user's open logins: an agent's scope must never be stale."""
    user = _get(session, username)
    _check_agent_ext(user.role, ext, team_exts)
    user.ext = ext
    user.token_version += 1
    session.commit()


def end_sessions(session: Session, user: AppUser) -> None:
    """Logout: every login token issued before now stops working."""
    user.token_version += 1
    session.commit()


def disable_user(session: Session, username: str) -> None:
    user = _get(session, username)
    user.active = False
    user.token_version += 1
    session.commit()


def _get(session: Session, username: str) -> AppUser:
    user = session.scalars(select(AppUser).where(AppUser.username == username.strip().lower())).first()
    if user is None:
        raise ValueError(f"no user {username!r}")
    return user
