"""Forwards page API (staff only): the archive of forwarded problems, one problem's story, the period's numbers
(spec 2026-11-16-priority-no-followup, "Part 4: forwards page"). Read-only."""

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from ..config import RIYADH


class DecideIn(BaseModel):
    hop_id: int


def register(app: FastAPI, *, DB, Manager, audit_analysis, cfg=None) -> None:

    @app.get("/api/forwards")
    def forwards(user: Manager, s: DB,
                 date_from: Annotated[date | None, Query(alias="from")] = None,
                 date_to: Annotated[date | None, Query(alias="to")] = None,
                 state: str | None = None, level: int | None = None, branch: str | None = None,
                 person: int | None = None) -> dict:
        from .. import forward_report as fr
        today = datetime.now(RIYADH).date()
        end = date_to or today
        start = date_from or end - timedelta(days=29)
        if start > end or (end - start).days >= fr.MAX_DAYS:
            raise HTTPException(422, "bad date range (at most 366 days)")
        if state is not None and state not in fr.STATES:
            raise HTTPException(422, "bad state")
        if level is not None and level not in (1, 2, 3):
            raise HTTPException(422, "bad level")
        if branch is not None and len(branch) > 130:
            raise HTTPException(422, "bad branch")
        out = fr.listing(s, start, end, state=state or None, level=level, branch=branch or None, person_id=person)
        out["from"], out["to"] = start.isoformat(), end.isoformat()
        return out

    @app.get("/api/kpi/forwards")
    def forward_kpi(user: Manager, s: DB,
                    date_from: Annotated[date | None, Query(alias="from")] = None,
                    date_to: Annotated[date | None, Query(alias="to")] = None,
                    agent: Annotated[str | None, Query(pattern=r"^\d{1,6}$")] = None) -> dict:
        """D4 (req 20): the people problems were forwarded to (late = 24 h ran out, one line each) and the
        agents' answers to their questions. Staff only, show-only, by the KPI page's من/إلى."""
        from .. import forward_report as fr
        today = datetime.now(RIYADH).date()
        end = date_to or today
        start = date_from or end - timedelta(days=6)
        if start > end or (end - start).days >= fr.MAX_DAYS:
            raise HTTPException(422, "bad date range (at most 366 days)")
        return fr.people_kpi(s, start, end, agent=agent)

    @app.get("/api/forwards/{call_id}")
    def forward_story(call_id: int, user: Manager, s: DB) -> dict:
        from .. import forward_report as fr
        got = fr.story(s, call_id)
        if got is None:
            raise HTTPException(404, "no forwarded problem on this call")
        audit_analysis(s, user, call_id)        # notes + satisfaction scores: audited like the analysis view
        return got

    @app.post("/api/forwards/{call_id}/decide")
    def forward_decide(call_id: int, body: DecideIn, user: Manager, s: DB) -> dict:
        """User decision 2026-11-22: a supervisor / admin closes the forwarded problem and picks who in the chain
        solved it (his KPI point). Our side at once; the worker closes the ticket(s) there."""
        from datetime import timezone
        from .. import forward
        from .. import forward_report as fr
        from ..db.models import AdminLog
        now = datetime.now(timezone.utc)
        try:
            h = forward.decide(s, call_id, body.hop_id, user.id, now)
        except forward.DecideError as exc:
            s.rollback()
            raise HTTPException(409, str(exc)) from None
        s.add(AdminLog(user_id=user.id, action="forward_decide", target=f"call:{call_id} hop:{h.id}", at=now))
        s.commit()
        if cfg is not None and cfg.forward_send is not None:
            try:
                cfg.forward_send()          # close there now; the 5-minute job does it anyway
            except Exception:  # noqa: BLE001  saved here, the close there is only sooner
                pass
        got = fr.story(s, call_id)
        audit_analysis(s, user, call_id)
        return got
