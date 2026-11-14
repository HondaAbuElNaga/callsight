# Spec: Live call board (+ Listen, switched off)

**Roadmap items:** Phase 5: "Live event stream from PBX adapter → WebSocket → live board", "Listen button"
(built behind a switch that stays OFF: the user paused silent listen), "Access control + audit log of who listened".
**Status:** draft

## Goal
The supervisor sees all active team calls (who is talking, caller, duration, queue waiting) updating within 2 s
(Phase 5 Done when: "live board updates within 2 s of call state change; Listen rings the supervisor's phone and
connects silently").

## Facts (2026-11-14)
- Yeastar P-Series WebSocket: `wss://<pbx>/openapi/v1.0/subscribe?access_token=…`, send `{"topic_list":[…]}`,
  heartbeat text "heartbeat" every ≤ 60 s; topics: 30011 call status changes, 30012 call end (CDR-like),
  30008 extension status (per Yeastar docs; `tools/listen_test.py` subscribes to 30011 and a 30011 payload was
  never captured yet). The subscribe call itself worked earlier (pbx-discovery tasks).
- **8 valid tokens max** (errcode 60002); tokens last 30 min; failed auth may auto-block the IP. Our ingest already
  holds one token. The PC is off the office LAN tonight → **no PBX contact at all tonight** (judges' decision R7).
- Listen: `POST call/listen {monitor, channel_id, type:"listen"}`; `*51119` was declined while 119 talked; Listen
  feature **paused by the user** 2026-11-12.

## Scope
- In:
  - `callsight/live/`: event parser (30011/30012/30008 → `LiveCall` / `ExtensionState` models, tolerant of unknown
    fields), in-memory state (calls by call id, extension states, queue waiting list), pruning of calls missed by a
    lost end event (> 4 h or on reconnect snapshot).
  - **One** PBX WebSocket connection in a dedicated `live` service (profile `live`, **off by default**,
    `LIVE_ENABLED=false`), reusing the ingest token logic (shares the token via Redis, never more than one extra
    login; never retries failed auth; exponential back-off 5 s → 5 min when the PBX is unreachable).
    State published to Redis (pub/sub + a snapshot key).
  - API WebSocket `/api/live` (admin + supervisor, cookie auth) pushes the snapshot then deltas from Redis.
  - **Live page** "المكالمات الحالية": cards per active call (agent, masked caller, direction, state ringing / talking
    / holding, live timer, queue), queue waiting list, agent status row (idle / ringing / busy / offline);
    "no live data" state when the service is off or disconnected (with last-seen time).
  - **Listen:** API `POST /api/live/{call}/listen` exists but returns 403 "feature off" unless `LISTEN_ENABLED=true`
    (default false); the button is hidden while off. When on: only supervisor/admin with an `ext`, writes a
    `listen_audit` row (who, which call, agent, time, result) before calling the PBX. Not wired to the PBX tonight.
  - Tests with **synthetic** event fixtures written from the Yeastar docs (labelled synthetic).
- Out:
  - Real capture of 30011 payloads, live check on the office LAN, enabling Listen (⚠ NEEDS USER).
  - "Rang agent but not picked" per-agent counts from live events (later KPI improvement).

## Acceptance criteria
- [ ] pytest: parser on synthetic payloads (incl. unknown/missing fields), state transitions (ring → talk → hold →
      end, transfer, queue join/leave), pruning, back-off schedule, no auth retry after a failed login, token sharing
      (max one extra login), API WebSocket auth (401/close without login), snapshot + delta push, Listen 403 while off,
      audit row when on (PBX client faked)
- [ ] `docker compose up -d` starts no `live` service; the live page shows "no live data"
- [ ] ⚠ NEEDS USER (office LAN): `--profile live up -d live`, make a test call, board updates < 2 s; capture real
      30011 payloads to replace the synthetic fixtures; later decide on Listen

## Open questions (decided by 2 judges on the user's behalf)
1. **Token:** share the ingest token via Redis (recommended: stays within the 8-token limit) vs own login.
2. **Push:** API WebSocket (recommended; FastAPI has it) vs polling every 2 s.
3. **Caller number on the board:** masked (last 4 digits) by default (recommended) vs full.
4. **Build Listen code now** behind an OFF switch (recommended, roadmap) vs not at all until the user un-pauses.
