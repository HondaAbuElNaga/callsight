"""FastAPI app: JSON API under /api, recordings, and the built web page at /.

Run: uvicorn callsight.api.main:app  (settings from .env; APP_SECRET is required)
"""

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Callable, Iterator

from fastapi import Depends, FastAPI, HTTPException, Path as FPath, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import kpi, transcripts
from ..config import RIYADH
from ..db.models import (Agent, AppUser, Call, GoldClip, ListenAudit, RecordingPlay, ReportArchive, ReportDownload,
                         TranscriptAccess)
from ..ingest.classify import TeamConfig
from ..pbx.base import PbxError
from . import auth
from . import live as live_ws
from .live import LiveConfig, NoLiveSource
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
    asr_model: str = "whisper-large-v3"   # ASR_MODEL: its done transcript is the one displayed
    health: Callable[[Session], dict] | None = None    # health page data (callsight.health.collect)
    live: "LiveConfig | None" = None                   # live board WebSocket (None: always "no live data")
    # Listen (spec live-board, decision 4): OFF. Gate 1 = this flag, read on EVERY request (LISTEN_ENABLED);
    # gate 2 = the PBX adapter's listen() refuses too. Every attempt is written to listen_audit.
    listen_enabled: Callable[[], bool] = lambda: False
    listen_client: Callable[[], object] | None = None   # -> object with listen(monitor_ext, call_id)
    live_lookup: Callable[[str], dict | None] | None = None   # live call by PBX call id (from the snapshot)


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


def audit_transcript(s: Session, user: AppUser, call_id: int, kind: str) -> None:
    """Transcripts are personal data: views (deduplicated within AUDIT_WINDOW, like plays) and every export."""
    now = datetime.now(timezone.utc)
    if kind == "view" and s.scalar(select(TranscriptAccess.id).where(
            TranscriptAccess.user_id == user.id, TranscriptAccess.call_id == call_id, TranscriptAccess.kind == kind,
            TranscriptAccess.accessed_at > now - AUDIT_WINDOW).limit(1)) is not None:
        return
    s.add(TranscriptAccess(user_id=user.id, call_id=call_id, kind=kind, accessed_at=now))
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
        if request.url.path.startswith("/api/") and "cache-control" not in resp.headers:
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
        words: Annotated[str | None, Query()] = None,   # checked by hand: a 422 must not echo the words
    ) -> CallFilter:
        today = datetime.now(RIYADH).date()
        try:
            parsed = transcripts.parse_words(words)
        except transcripts.BadSearch as exc:
            raise HTTPException(422, str(exc)) from None
        scope = visible_scope(user)
        if scope.is_agent:
            agent = scope.agent_ext          # the server decides an agent's filter, whatever the URL says
        f = CallFilter(scope, date_from or today, date_to or date_from or today, agent or None,
                       status or None, direction or None, q or None, parsed, cfg.asr_model)
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

    def search_allowed(user: AppUser, f: CallFilter) -> None:
        if f.words and user.role not in auth.STAFF_ROLES:   # word search: staff only
            raise HTTPException(403, "transcript search: admin or supervisor only")

    @app.get("/api/calls")
    def calls(user: User, s: DB, f: Filter, page: Annotated[int, Query(ge=1, le=10_000)] = 1) -> dict:
        search_allowed(user, f)
        return list_calls(s, f, page)

    @app.get("/api/calls/summary")
    def calls_summary(user: User, s: DB, f: Filter) -> dict:
        search_allowed(user, f)
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

    # ---------- transcripts (staff: all; agents: own calls only; every view/export audited) ----------

    @app.get("/api/calls/{call_id}/transcript")
    def transcript(call_id: int, user: User, s: DB) -> dict:
        if visible_call(s, call_id, visible_scope(user)) is None:
            raise HTTPException(404, "no transcript")     # other agents' calls: not even confirmed
        t = transcripts.displayed(s, call_id, cfg.asr_model)
        if t is None:
            raise HTTPException(404, "no transcript")
        if t.status == "done":                   # status polling (queued/running) writes no audit row
            audit_transcript(s, user, call_id, "view")
        return transcripts.as_dict(s, t)

    @app.get("/api/calls/{call_id}/transcript.txt")
    def transcript_txt(call_id: int, user: User, s: DB) -> Response:
        c = visible_call(s, call_id, visible_scope(user))
        t = transcripts.displayed(s, call_id, cfg.asr_model) if c is not None else None
        if t is None or c is None or t.status != "done":
            raise HTTPException(404, "no transcript")
        ext = c.agent_ext or c.missed_agent_ext
        agent = s.get(Agent, ext) if ext else None
        data = transcripts.export_txt(c, agent.name if agent else None, t, transcripts.segments(s, t))
        audit_transcript(s, user, call_id, "export")
        return Response(data, media_type="text/plain; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="{transcripts.txt_filename(c)}"',
            "Cache-Control": "private, no-store"})

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

    # ---------- health (admin only; no paths, hostnames or error texts in the answer) ----------

    @app.get("/api/health")
    def health(user: Admin, s: DB) -> dict:
        if cfg.health is None:
            raise HTTPException(503, "health checks not configured")
        return cfg.health(s)

    # ---------- live board (admin + supervisor) + Listen stub (OFF) ----------

    live_ws.register(app, cfg.live or LiveConfig(origins=frozenset(), source=NoLiveSource()), session_factory,
                     cfg.secret)

    def listen_audit(s: Session, user: AppUser, ip: str | None, call_id: str, result: str, agent: str | None = None,
                     kind: str = "attempt", attempt_id: int | None = None) -> ListenAudit:
        row = ListenAudit(kind=kind, result=result, attempt_id=attempt_id, user_id=user.id, user_ext=user.ext,
                          client_ip=ip, live_call_id=call_id, agent_ext=agent)
        s.add(row)
        s.commit()                       # committed before anything else happens (before any PBX call)
        return row

    @app.post("/api/live/{call_id}/listen")
    def listen(call_id: Annotated[str, FPath(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")], request: Request, user: User,
               s: DB) -> dict:
        ip = request.client.host if request.client else None
        if user.role not in auth.STAFF_ROLES:
            listen_audit(s, user, ip, call_id, "denied_role")
            raise HTTPException(403, "admin or supervisor only")
        if not cfg.listen_enabled():
            listen_audit(s, user, ip, call_id, "denied_off")
            raise HTTPException(403, "feature off")
        if not user.ext:
            listen_audit(s, user, ip, call_id, "denied_no_ext")
            raise HTTPException(403, "your account has no extension to ring")
        live = cfg.live_lookup(call_id) if cfg.live_lookup else None
        agent = ((live or {}).get("agents") or [None])[0]
        if live is None or agent is None:
            listen_audit(s, user, ip, call_id, "not_found", agent)
            raise HTTPException(404, "no such call talking now")
        attempt = listen_audit(s, user, ip, call_id, "pending", agent)
        try:
            if cfg.listen_client is None:
                raise RuntimeError("no PBX client")
            cfg.listen_client().listen(user.ext, call_id)       # the adapter refuses too (2nd gate)
            result = "ok"
        except PbxError:
            result = "refused"
        except Exception:  # noqa: BLE001 - audited; details stay out of the answer
            result = "error"
        listen_audit(s, user, ip, call_id, result, agent, kind="result", attempt_id=attempt.id)
        if result != "ok":
            raise HTTPException(503, "the PBX did not start listening")
        return {"ok": True}

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
        asr_model=os.getenv("ASR_MODEL", "whisper-large-v3").strip(),
        cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes"),
        health=_health_from_env(), live=_live_from_env(),
        listen_enabled=lambda: os.getenv("LISTEN_ENABLED", "false").strip().lower() in ("1", "true", "yes"),
        listen_client=lambda: _listen_adapter(), live_lookup=_live_lookup_from_env()))


def live_origins(env: dict[str, str] | os._Environ = os.environ) -> frozenset[str]:
    """LIVE_ORIGINS="http://127.0.0.1:8080,http://localhost:8080" (exact match); default: this PC on WEB_PORT."""
    raw = env.get("LIVE_ORIGINS", "").strip()
    if raw:
        return frozenset(o.strip().rstrip("/") for o in raw.split(",") if o.strip())
    port = env.get("WEB_PORT", "8080").strip() or "8080"
    return frozenset({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})


def _live_from_env() -> LiveConfig:
    from ..app import settings
    return LiveConfig(origins=live_origins(), source=live_ws.RedisLiveSource(settings().redis_url))


def _live_lookup_from_env() -> Callable[[str], dict | None]:
    import json

    from ..app import redis_client
    from ..live.bus import SNAPSHOT_KEY

    def lookup(call_id: str) -> dict | None:
        raw = redis_client().get(SNAPSHOT_KEY)
        calls = (json.loads(raw) or {}).get("calls") or [] if raw else []
        return next((c for c in calls if c.get("call_id") == call_id), None)
    return lookup


def _listen_adapter():
    """Only reached when LISTEN_ENABLED=true; its listen() still refuses (listen_enabled=False, 2nd gate)."""
    from ..app import pbx_adapter
    return pbx_adapter("api", fail_closed=True)


def _health_from_env(cache_seconds: float = 15.0) -> Callable[[Session], dict]:
    """Real disks / Redis / Celery for the health page; answers cached a few seconds (worker ping takes 1 s)."""
    import time

    import redis

    from .. import health
    from ..app import settings
    from ..jobs import celery
    st = settings()
    deps = health.HealthDeps(
        disk_paths={"system": Path("/"), "audio": st.audio_dir, "backup": st.backup_dir},
        redis=redis.Redis.from_url(st.redis_url, socket_timeout=1, socket_connect_timeout=1),
        ping=lambda: celery.control.ping(timeout=1.0), offsite_confirmed=health.offsite_confirmed())
    cached: dict = {}

    def get(s: Session) -> dict:
        if not cached or time.monotonic() - cached["t"] > cache_seconds:
            cached.update(t=time.monotonic(), data=health.collect(s, deps))
        return cached["data"]
    return get


def __getattr__(name: str):   # `uvicorn callsight.api.main:app` builds the app lazily (tests import without .env)
    if name == "app":
        return _app_from_env()
    raise AttributeError(name)
