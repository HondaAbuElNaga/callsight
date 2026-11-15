# Product Mission

## Pitch
**CallSight** (working name) helps a **small customer-service and after-sales team**
**see the quality of every phone call** by **pulling calls from the office IP-PBX,
transcribing them in Saudi Arabic, scoring customer satisfaction 1–10 with a local
fine-tuned model, and producing weekly/monthly KPI reports**. Everything runs on
servers that only the company controls (since 2026-11-15: AWS, region Frankfurt; before: one PC in the office);
no audio or text is sent to an outside AI service.

## Users
| User | Pain today | What they get |
|---|---|---|
| Manager / supervisor | No visibility of call quality, missed calls or repeat callers; can't listen live without standing next to the agent | Live board of active calls, silent listen from own Yealink phone, one-click PDF KPI reports |
| Customer service agents (2) | Performance judged by feeling, not numbers | Fair, transparent KPIs and their own call history |
| After-sales agent (1) | Repeat callers and unresolved issues are invisible | 72h repeat-call tracking and per-call transcript |

## Problems and solutions
| Problem | Solution |
|---|---|
| Don't know how many calls were missed (unanswered/abandoned) | Read PBX call records (CDR) + live events, classify answered / no-answer / abandoned / busy |
| Don't know how long calls take | Duration, ring time, talk time, hold time per call → AHT |
| Don't know if the customer was satisfied | Saudi-Arabic transcription + fine-tuned sentiment model → 1–10 satisfaction score per call |
| Can't search or read what was said | Full transcript per call, split agent / customer |
| Can't supervise live | Live call board + silent listen (spy) from the supervisor's phone |
| Customers call back because the issue wasn't solved | Repeat-call detection: same number calls again within 72h |
| Reports are manual | Button → weekly / monthly PDF report per agent and team |

## Users of the system (initial)
- 2 customer-service agents, 1 after-sales agent, 1+ supervisor.
- Under 200 calls/day. Design must scale to more agents/calls by adding workers, not rewriting.

## KPI definitions (the contract for reports)
| KPI | Definition |
|---|---|
| Number of calls | All inbound calls that reached the team (queue/ring group) in the period; per agent = calls answered by that agent |
| Unanswered calls | Inbound calls never answered by an agent (no-answer, abandoned in queue, busy). Per agent: calls that rang the agent and were not picked up |
| AHT (average handling time) | (talk time + hold time) ÷ answered calls. After-call work is added later if the PBX exposes it |
| Average satisfaction | Mean of the 1–10 score over analysed answered calls |
| Repeat calls (72h) | Answered call whose caller number had another call within the previous 72h; counted against the agent who handled the earlier call |

## Success metrics
- 100% of PBX calls appear in the system within 15 min of hang-up (CDR reconciliation shows 0 missing).
- Transcription word error rate on our own Saudi calls ≤ 25% (measured on 30 hand-checked calls).
- Satisfaction score agrees with a human rating within ±2 points on ≥ 80% of a 100-call test set.
- Weekly + monthly PDF generated in < 30 s with one click.
- Supervisor can start silent listening on any live call in ≤ 3 clicks.
