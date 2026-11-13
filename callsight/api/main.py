"""FastAPI app: JSON API under /api, recordings, and the built web page at /.

Run: uvicorn callsight.api.main:app  (settings from .env; APP_SECRET is required)
"""

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Callable, Iterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import RIYADH
from ..db.models import AppUser, Call, RecordingPlay
from . import auth
from .calls import DIRECTIONS, STATUSES, CallFilter, call_detail, list_calls, summary, team_agents

MAX_RANGE_DAYS = 400


@dataclass(frozen=True)
class ApiConfig:
    secret: str
    audio_dir: Path
    static_dir: Path | None = None
    cookie_secure: bool = False


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


def create_app(session_factory: Callable[[], Session], cfg: ApiConfig) -> FastAPI:
    if len(cfg.secret) < 32:
        raise RuntimeError("APP_SECRET must be at least 32 characters")
    app = FastAPI(title="CallSight", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; media-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def db() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    def current_user(request: Request, s: Annotated[Session, Depends(db)]) -> AppUser:
        user = auth.user_from_token(s, request.cookies.get(auth.COOKIE_NAME), cfg.secret)
        if user is None:
            raise HTTPException(401, "login required")
        return user

    DB = Annotated[Session, Depends(db)]
    User = Annotated[AppUser, Depends(current_user)]

    def call_filter(
        date_from: Annotated[date | None, Query(alias="from")] = None,
        date_to: Annotated[date | None, Query(alias="to")] = None,
        agent: Annotated[str | None, Query(pattern=r"^\d{1,6}$")] = None,
        status: Annotated[str | None, Query()] = None,
        direction: Annotated[str | None, Query()] = None,
        q: Annotated[str | None, Query(pattern=r"^\+?\d{1,20}$")] = None,
    ) -> CallFilter:
        today = datetime.now(RIYADH).date()
        f = CallFilter(date_from or today, date_to or date_from or today, agent or None,
                       status or None, direction or None, q or None)
        if f.date_to < f.date_from or (f.date_to - f.date_from).days > MAX_RANGE_DAYS:
            raise HTTPException(422, "bad date range")
        if f.status and f.status not in STATUSES:
            raise HTTPException(422, "unknown status")
        if f.direction and f.direction not in DIRECTIONS:
            raise HTTPException(422, "unknown direction")
        return f

    Filter = Annotated[CallFilter, Depends(call_filter)]

    # ---------- auth ----------

    @app.post("/api/login")
    def login(body: LoginIn, s: DB) -> Response:
        try:
            user = auth.authenticate(s, body.username, body.password)
        except auth.LoginError as exc:
            return JSONResponse({"detail": str(exc), "locked": exc.locked}, status_code=429 if exc.locked else 401)
        resp = JSONResponse({"username": user.username, "role": user.role})
        resp.set_cookie(auth.COOKIE_NAME, auth.make_token(user, cfg.secret), max_age=auth.TOKEN_HOURS * 3600,
                        httponly=True, samesite="strict", secure=cfg.cookie_secure, path="/")
        return resp

    @app.post("/api/logout")
    def logout() -> Response:
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.COOKIE_NAME, path="/")
        return resp

    @app.get("/api/me")
    def me(user: User) -> dict:
        return {"username": user.username, "role": user.role, "ext": user.ext}

    # ---------- calls ----------

    @app.get("/api/agents")
    def agents(user: User, s: DB) -> list[dict]:
        return team_agents(s)

    @app.get("/api/calls")
    def calls(user: User, s: DB, f: Filter, page: Annotated[int, Query(ge=1, le=10_000)] = 1) -> dict:
        return list_calls(s, f, page)

    @app.get("/api/calls/summary")
    def calls_summary(user: User, s: DB, f: Filter) -> dict:
        return summary(s, f)

    @app.get("/api/calls/{call_id}")
    def call(call_id: int, user: User, s: DB) -> dict:
        d = call_detail(s, call_id)
        if d is None:
            raise HTTPException(404, "no such call")
        return d

    @app.get("/api/calls/{call_id}/recording")
    def recording(call_id: int, request: Request, user: User, s: DB) -> Response:
        c = s.get(Call, call_id)
        if c is None or c.recording_status != "ok" or not c.recording_path:
            raise HTTPException(404, "no recording")
        root = cfg.audio_dir.resolve()
        path = (root / c.recording_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "no recording")
        rng = request.headers.get("range", "")
        if not rng or rng.replace(" ", "").startswith("bytes=0-"):   # one audit row per play, not per seek
            s.add(RecordingPlay(user_id=user.id, call_id=c.id, played_at=datetime.now(timezone.utc)))
            s.commit()
        return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "private, no-store"})

    @app.get("/api/{rest:path}")
    def api_404(rest: str) -> None:
        raise HTTPException(404, "not found")

    if cfg.static_dir and (cfg.static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=cfg.static_dir, html=True), name="web")

    return app


def _app_from_env() -> FastAPI:
    from ..app import session_factory, settings
    secret = os.getenv("APP_SECRET", "")
    static = Path(os.getenv("WEB_DIST", str(Path(__file__).resolve().parents[2] / "web" / "dist")))
    return create_app(session_factory(), ApiConfig(
        secret=secret, audio_dir=settings().audio_dir, static_dir=static,
        cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")))


def __getattr__(name: str):   # `uvicorn callsight.api.main:app` builds the app lazily (tests import without .env)
    if name == "app":
        return _app_from_env()
    raise AttributeError(name)
