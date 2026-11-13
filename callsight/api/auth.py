"""Passwords (Argon2), login token (signed JWT in an HttpOnly cookie), lockout after wrong passwords."""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AppUser

COOKIE_NAME = "cs_session"
TOKEN_HOURS = 8
MAX_FAILED = 5
LOCK_MINUTES = 15
MIN_PASSWORD = 10
ROLES = ("admin", "supervisor")

_ph = PasswordHasher()
_DUMMY_HASH = _ph.hash("timing-equaliser-not-a-password")


class LoginError(Exception):
    def __init__(self, message: str, locked: bool = False):
        super().__init__(message)
        self.locked = locked


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
    """Returns the user or raises LoginError. Same message for unknown user and wrong password."""
    now = now or datetime.now(timezone.utc)
    user = session.scalars(select(AppUser).where(AppUser.username == username.strip().lower())).first()
    if user is None or not user.active:
        _verify(_DUMMY_HASH, password)          # same time as a real check
        raise LoginError("wrong username or password")
    if user.locked_until and user.locked_until > now:
        raise LoginError("account locked for a few minutes after too many wrong passwords", locked=True)
    if not _verify(user.password_hash, password):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until, user.failed_logins = now + timedelta(minutes=LOCK_MINUTES), 0
        session.commit()
        raise LoginError("wrong username or password")
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


# ---------- account management (CLI) ----------

def create_user(session: Session, username: str, password: str, role: str, ext: str | None = None) -> AppUser:
    username = username.strip().lower()
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
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
