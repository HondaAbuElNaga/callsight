# Spec: PBX discovery (Yeastar P-Series)

**Roadmap item:** Phase 0, "PBX discovery & server"
**Status:** done (closed 2026-11-18; Listen moved to specs/2026-11-17-listen)

## Goal
Prove we can get, from the Yeastar P-Series PBX, everything the mission needs:
call records (duration, status), recordings, live call events and silent listening.
Every later phase depends on this, so we confirm it before writing app code.

## What Yeastar P-Series offers (from the official docs)
| Need | Yeastar feature | Source |
|---|---|---|
| Call records + duration + status | API `cdr/list`, `cdr/search`, `cdr/detail` (CDR 2.0), `cdr/download` | [API summary](https://help.yeastar.com/en/p-series-appliance-edition/developer-guide/api-interfaces-and-events-summary.html) |
| Recordings | API `recording/list`, `recording/search`, `recording/download` | same |
| Live calls | WebSocket/Webhook events 30011 *Call State Changed*, 30012 *Call End Details* (new CDR), 30008 *Extension Call State Changed*, 30029 *Agent Status Changed* | same; [Events overview](https://help.yeastar.com/en/p-series/developer-guide/api-events-overview.html) |
| Silent listen | Feature code `*51` + extension (Listen); API `call/listen` with `type=listen` | [Call monitoring](https://help.yeastar.com/en/p-series/administrator-guide/call-monitoring-overview.html), [Feature code permission](https://help.yeastar.com/en/p-series/administrator-guide/allow-users-to-monitor-a-call-by-dialing-a-feature-code.html) |
| Queues / agents | API `queue/list`, `queue/call_status`, `queue/agent_status` | API summary |
| Enable API | Admin portal → Integrations → API → enable → Client ID + Client Secret, optional IP allow-list. **Needs Enterprise or Ultimate plan.** Tokens expire after 30 min; every request needs a `User-Agent` header | [Enable API](https://help.yeastar.com/en/p-series/developer-guide/enable-yeastar-p-series-pbx-api.html) |

## Scope
- In: identify edition/model/firmware/plan; enable API; get credentials; confirm CDR,
  recording and event access with a script; enable recording and Listen permission;
  write `docs/pbx-discovery.md`; server hardware decision.
- Out: any app/database/UI code (Phase 1). Live transcript (rejected idea LIVE-1).

## Requirements
1. Record PBX edition (Appliance / Software / Cloud), model, firmware version, subscription plan.
2. API is enabled with a dedicated Client ID/Secret, restricted to the analytics server's IP.
3. A Python probe script (`tools/pbx_probe.py`, read-only) can:
   a. get and refresh a token;
   b. list CDRs for the last 7 days and show, per call: time, caller, callee/agent, ring time, talk time, status/disposition;
   c. list and download one recording, and report its format (codec, sample rate, mono/stereo);
   d. connect to the WebSocket, subscribe to 30011 + 30012 + 30008, and print events during one test call.
4. Call recording is on for all inbound calls of the 3 agents (and legal announcement played).
5. Supervisor extension has **Listen** permission only (not Whisper/Barge-in); `*51<ext>` works from the Yealink phone.
6. Map the 3 agents → extension numbers and team (customer service ×2, after-sales ×1); note queue / ring-group names.
7. Credentials live only in `.env` (git-ignored), never in code or docs.

## Acceptance criteria
- [ ] `docs/pbx-discovery.md` filled: edition, model, firmware, plan, queues, extensions, recording format, CDR fields available
- [ ] `python tools/pbx_probe.py token` prints "token OK" and a refresh succeeds
- [ ] `python tools/pbx_probe.py cdr --days 7` prints ≥ 1 row with talk time and status; the count matches the PBX Call Logs page for the same day
- [ ] `python tools/pbx_probe.py recording --latest` saves a playable audio file and prints its format
- [ ] `python tools/pbx_probe.py events` prints 30011 and 30012 events during a live test call
- [ ] Supervisor dials `*51<agent ext>` during a test call: hears both sides, the agent and customer hear nothing
- [ ] An unanswered test call shows as NO ANSWER/abandoned in the CDR with ring time

## Technical approach
- Python 3.12, `httpx` (REST) + `websockets` (events) + `python-dotenv`. Read-only API calls only.
- Probe output is saved as JSON samples in `docs/samples/` (phone numbers masked), which later
  become test fixtures for the Phase 1 `YeastarAdapter`.
- Risks:
  - **Plan below Enterprise → no API.** Fallbacks: upgrade plan (recommended), or scheduled CDR CSV
    export + recording export via the admin UI/FTP-network drive (more fragile, no live board).
  - **Recordings likely mono (both voices mixed).** Then agent/customer separation needs speaker
    diarization in Phase 2 (pyannote, as in whisper-diarization) instead of stereo channels; we confirm the format here.
  - Older firmware may lack CDR 2.0 (`cdr/detail`); then use CDR 1.0 fields.

## Open questions
1. **Which plan is the PBX on?** (Admin portal → System → License/Plan.) Recommendation: if not Enterprise/Ultimate, upgrade. The API is the cleanest way to get calls out and the only way to get a live board.
2. **Who has the admin login?** The screenshot is the user portal (Linkus). We need the admin portal, usually `https://<pbx-ip>:8088` or the Yeastar cloud domain. Recommendation: the IT person or Yeastar reseller creates a separate admin account for this project.
3. **Do customers call a queue or a ring group?** Queues give better abandoned-call data. Recommendation: use a queue if one isn't set up already.
4. **Git:** the build steps need git (versioning, review before changes). Recommendation: `git init` locally; no GitHub push unless you want it.

## Decisions
1. User started the work 2026-11-12 (approval). Admin portal access confirmed; Integrations → API enabled by user.
2. Recordings exist and are on (Reports and Recordings page); file sizes (~16 KB/s) suggest 8 kHz mono WAV, to confirm with the probe.
3. Calls are mostly **outbound**; KPIs must cover inbound and outbound separately (to confirm with user).
4. Git: not initialised yet (user declined for now).
5. Connect over LAN `https://192.168.50.151:8088` (confirmed working); remote FQDN API not used.
6. Recording format confirmed by probe: mono WAV 8 kHz 16-bit → Phase 2 uses diarization.
7. Inbound calls pass IVR 6200; IVR-only "ANSWERED" rows are not agent-answered calls.
