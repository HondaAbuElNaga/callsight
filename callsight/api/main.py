"""FastAPI app: JSON API under /api, recordings, and the built web page at /.

Run: uvicorn callsight.api.main:app  (settings from .env; APP_SECRET is required)
"""

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Callable, Iterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import kpi
from ..config import RIYADH
from ..db.models import AppUser, Call, GoldClip, RecordingPlay, ReportArchive, ReportDownload
from ..ingest.classify import TeamConfig
from . import auth
from .calls import (DIRECTIONS, STATUSES, CallFilter, call_detail, list_calls, summary, team_agents, visible_call,
                    visible_scope)

MAX_RANGE_DAYS = 400
AUDIT_WINDOW = timedelta(minutes=10)   # one audit row per user + call within this window


@dataclass(frozen=True)
class ApiConfig:
    secret: str
    audio_dir: Path
    static_dir: Path | None = None
    cookie_secure: bool = False
    teams: Callable[[], TeamConfig] = TeamConfig      # re-read per request (teams.yaml edits apply at once)
    reports_dir: Path | None = None


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class GoldTextIn(BaseModel):
    text: str = Field(max_length=10_000)


def audit_play(s: Session, user: AppUser, call_id: int) -> None:
    """Audit every recording access, whatever the Range header; seeks within AUDIT_WINDOW count as the same play."""
    now = datetime.now(timezone.utc)
    recent = s.scalar(select(RecordingPlay.id).where(
        RecordingPlay.user_id == user.id, RecordingPlay.call_id == call_id,
        RecordingPlay.played_at > now - AUDIT_WINDOW).limit(1))
    if recent is None:
        s.add(RecordingPlay(user_id=user.id, call_id=call_id, played_at=now))
        s.commit()


def audit_report(s: Session, user: AppUser, period: kpi.Period, scope: str, archive_id: int | None = None) -> None:
    """Every report download is logged (who, which report), like recording plays."""
    s.add(ReportDownload(user_id=user.id, archive_id=archive_id, period_type=period.kind, period_start=period.start,
                         scope=scope))
    s.commit()


@dataclass(frozen=True)
class KpiQuery:
    period: kpi.Period
    team: str | None
    agent: str | None

    @property
    def scope(self) -> str:
        return f"agent:{self.agent}" if self.agent else (f"team:{self.team}" if self.team else "all")


def pdf_response(data: bytes, name: str) -> Response:
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "private, no-store"})


def create_app(session_factory: Callable[[], Session], cfg: ApiConfig) -> FastAPI:
    if len(cfg.secret) < 32 or cfg.secret.lower().startswith("change-me"):
        raise RuntimeError("APP_SECRET must be a random text of at least 32 characters (not the .env.example value)")
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
        user: User,
        date_from: Annotated[date | None, Query(alias="from")] = None,
        date_to: Annotated[date | None, Query(alias="to")] = None,
        agent: Annotated[str | None, Query(pattern=r"^\d{1,6}$")] = None,
        status: Annotated[str | None, Query()] = None,
        direction: Annotated[str | None, Query()] = None,
        q: Annotated[str | None, Query(pattern=r"^\+?\d{1,20}$")] = None,
    ) -> CallFilter:
        today = datetime.now(RIYADH).date()
        scope = visible_scope(user)
        if scope.is_agent:
            agent = scope.agent_ext          # the server decides an agent's filter, whatever the URL says
        f = CallFilter(scope, date_from or today, date_to or date_from or today, agent or None,
                       status or None, direction or None, q or None)
        if f.date_to < f.date_from or (f.date_to - f.date_from).days > MAX_RANGE_DAYS:
            raise HTTPException(422, "bad date range")
        if f.status and f.status not in STATUSES:
            raise HTTPException(422, "unknown status")
        if f.direction and f.direction not in DIRECTIONS:
            raise HTTPException(422, "unknown direction")
        return f

    Filter = Annotated[CallFilter, Depends(call_filter)]

    def manager(user: User) -> AppUser:
        if user.role not in auth.STAFF_ROLES:
            raise HTTPException(403, "admin or supervisor only")
        return user

    Manager = Annotated[AppUser, Depends(manager)]

    def admin(user: User) -> AppUser:
        if user.role != "admin":
            raise HTTPException(403, "admin only")
        return user

    Admin = Annotated[AppUser, Depends(admin)]

    def kpi_query(
        user: User,
        period: Annotated[str, Query(pattern=r"^(week|month)$")] = "week",
        start: Annotated[date | None, Query()] = None,
        team: Annotated[str | None, Query(pattern=r"^[a-z_]{1,50}$")] = None,
        agent: Annotated[str | None, Query(pattern=r"^\d{1,6}$")] = None,
    ) -> KpiQuery:
        day = start or datetime.now(RIYADH).date()
        if not date(2020, 1, 1) <= day <= date(2100, 1, 1):
            raise HTTPException(422, "bad start date")
        teams = cfg.teams()
        scope = visible_scope(user)
        if scope.is_agent:                   # own KPIs only: no team view, no comparison with colleagues
            if not scope.agent_ext or scope.agent_ext not in teams.ext_team:
                raise HTTPException(403, "your extension is not a team agent (teams.yaml)")
            team, agent = None, scope.agent_ext
        if team and team not in kpi.team_names(teams):
            raise HTTPException(422, "unknown team")
        if agent and (agent not in teams.ext_team or (team and teams.ext_team[agent] != team)):
            raise HTTPException(422, "extension is not a team agent (teams.yaml)")
        return KpiQuery(kpi.period_for(period, day), team or None, agent or None)

    Kpi = Annotated[KpiQuery, Depends(kpi_query)]

    # ---------- auth ----------

    @app.post("/api/login")
    def login(body: LoginIn, s: DB) -> Response:
        try:
            user = auth.authenticate(s, body.username, body.password)
        except auth.LoginError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=401)
        resp = JSONResponse({"username": user.username, "role": user.role})
        resp.set_cookie(auth.COOKIE_NAME, auth.make_token(user, cfg.secret), max_age=auth.TOKEN_HOURS * 3600,
                        httponly=True, samesite="strict", secure=cfg.cookie_secure, path="/")
        return resp

    @app.post("/api/logout")
    def logout(request: Request, s: DB) -> Response:
        user = auth.user_from_token(s, request.cookies.get(auth.COOKIE_NAME), cfg.secret)
        if user is not None:
            auth.end_sessions(s, user)    # a copied cookie stops working too
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.COOKIE_NAME, path="/")
        return resp

    @app.get("/api/me")
    def me(user: User) -> dict:
        return {"username": user.username, "role": user.role, "ext": user.ext}

    # ---------- calls ----------

    @app.get("/api/agents")
    def agents(user: User, s: DB) -> list[dict]:
        return team_agents(s, visible_scope(user))

    @app.get("/api/calls")
    def calls(user: User, s: DB, f: Filter, page: Annotated[int, Query(ge=1, le=10_000)] = 1) -> dict:
        return list_calls(s, f, page)

    @app.get("/api/calls/summary")
    def calls_summary(user: User, s: DB, f: Filter) -> dict:
        return summary(s, f)

    @app.get("/api/calls/{call_id}")
    def call(call_id: int, user: User, s: DB) -> dict:
        d = call_detail(s, call_id, visible_scope(user))
        if d is None:
            raise HTTPException(404, "no such call")
        return d

    @app.get("/api/calls/{call_id}/recording")
    def recording(call_id: int, request: Request, user: User, s: DB) -> Response:
        c = visible_call(s, call_id, visible_scope(user))
        if c is None or c.recording_status != "ok" or not c.recording_path:
            raise HTTPException(404, "no recording")
        root = cfg.audio_dir.resolve()
        path = (root / c.recording_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "no recording")
        audit_play(s, user, c.id)
        return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "private, no-store"})

    # ---------- KPIs (agents: their own only) + reports (admin + supervisor) ----------

    @app.get("/api/kpi")
    def kpis(user: User, s: DB, q: Kpi) -> dict:
        return kpi.report(s, q.period, cfg.teams(), team=q.team, agent=q.agent)

    @app.get("/api/reports/pdf")
    def report_pdf(user: Manager, s: DB, q: Kpi) -> Response:
        from ..reports.pdf import filename, make_pdf
        try:
            data = make_pdf(s, q.period, cfg.teams(), team=q.team, agent=q.agent)
        except (ImportError, OSError) as exc:     # WeasyPrint/Pango missing (only outside the Docker image)
            raise HTTPException(503, "PDF engine not available") from exc
        audit_report(s, user, q.period, q.scope)
        return pdf_response(data, filename(q.period, q.team, q.agent))

    @app.get("/api/reports")
    def reports(user: Manager, s: DB) -> list[dict]:
        from ..reports.archive import list_archive
        return list_archive(s)

    @app.get("/api/reports/{report_id}/pdf")
    def archived_pdf(report_id: int, user: Manager, s: DB) -> Response:
        r = s.get(ReportArchive, report_id)
        if r is None or cfg.reports_dir is None:
            raise HTTPException(404, "no such report")
        root = cfg.reports_dir.resolve()
        path = (root / r.file).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "report file missing")
        audit_report(s, user, kpi.period_for(r.period_type, r.period_start), r.scope, r.id)
        return pdf_response(path.read_bytes(), path.name)

    # ---------- gold set (admin only): reference texts for the ASR benchmark ----------

    def clip_dict(c: GoldClip, call: Call | None, with_text: bool) -> dict:
        d = {"id": c.id, "call_id": c.call_id, "seconds": c.seconds, "has_text": bool(c.reference_text),
             "typed_at": c.typed_at.isoformat() if c.typed_at else None,
             "agent_ext": call.agent_ext if call else None, "direction": call.direction if call else None}
        if with_text:
            d["reference_text"] = c.reference_text or ""
        return d

    @app.get("/api/goldset")
    def goldset(user: Admin, s: DB) -> list[dict]:
        rows = s.execute(select(GoldClip, Call).join(Call, Call.id == GoldClip.call_id).order_by(GoldClip.id)).all()
        return [clip_dict(c, call, False) for c, call in rows]

    @app.get("/api/goldset/{clip_id}")
    def gold_clip(clip_id: int, user: Admin, s: DB) -> dict:
        c = s.get(GoldClip, clip_id)
        if c is None:
            raise HTTPException(404, "no such clip")
        return clip_dict(c, s.get(Call, c.call_id), True)

    @app.put("/api/goldset/{clip_id}")
    def save_gold_clip(clip_id: int, body: GoldTextIn, user: Admin, s: DB) -> dict:
        c = s.get(GoldClip, clip_id)
        if c is None:
            raise HTTPException(404, "no such clip")
        text = body.text.strip()
        c.reference_text = text or None
        c.typed_by, c.typed_at = (user.id, datetime.now(timezone.utc)) if text else (None, None)
        s.commit()
        return clip_dict(c, s.get(Call, c.call_id), True)

    @app.get("/api/goldset/{clip_id}/audio")
    def gold_audio(clip_id: int, user: Admin, s: DB) -> Response:
        c = s.get(GoldClip, clip_id)
        if c is None:
            raise HTTPException(404, "no such clip")
        root = cfg.audio_dir.resolve()
        path = (root / c.file).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "no audio")
        audit_play(s, user, c.call_id)
        return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "private, no-store"})

    @app.get("/api/{rest:path}")
    def api_404(rest: str) -> None:
        raise HTTPException(404, "not found")

    if cfg.static_dir and (cfg.static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=cfg.static_dir, html=True), name="web")

    return app


def _app_from_env() -> FastAPI:
    from ..app import reports_dir, session_factory, settings, teams
    secret = os.getenv("APP_SECRET", "")
    static = Path(os.getenv("WEB_DIST", str(Path(__file__).resolve().parents[2] / "web" / "dist")))
    return create_app(session_factory(), ApiConfig(
        secret=secret, audio_dir=settings().audio_dir, static_dir=static, teams=teams, reports_dir=reports_dir(),
        cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")))


def __getattr__(name: str):   # `uvicorn callsight.api.main:app` builds the app lazily (tests import without .env)
    if name == "app":
        return _app_from_env()
    raise AttributeError(name)
