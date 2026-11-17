# Spec: Listen — supervisor hears a live call silently

**Status:** draft (2026-11-17) · **Plan step:** 9 of [finish plan](../../product/finish-plan.md) — only after step 8
**Roadmap items:** Phase 5 "Listen button"; Phase 5 "Done when": board updates within 2 s; Listen rings the
supervisor's phone and connects silently.

## Goal
The supervisor clicks "استماع" on a live call; their Yealink phone (ext 114, or their own extension) rings; when
they pick up they hear the call; the customer and agent hear nothing. Today the button is a 403 stub (paused by
the user on 2026-11-12, legal steps first).

## Scope
**In:** Yeastar API `call/listen` through the existing PBX connection (sstli.ras.yeastar.com), the button on the
live board for staff, audit log (already built, append-only), PBX setting check.
**Out:** whisper (talk to the agent) and barge-in; listening to past calls (that is the recording player).

## Requirements
1. **PBX setting (YOU, click by click):** Extension 104/116/119 → Features → Call Monitor allows ext 114 (and any
   other supervisor ext) to Listen only. Earlier test: `*51119` was declined while 119 talked → this setting.
2. **Button:** on each team call on the live board, staff only; asks once "your phone will ring" and the
   supervisor's extension (from their account).
3. **Call:** `call/listen` with mode listen-only; errors shown in plain words (phone busy, PBX refused, call ended).
4. **Audit:** every try written (who, which call, result) — table `listen_audit` exists.
5. **Off switch:** `LISTEN_ENABLED` in `.env`; off until step 8 is done; the 403 stub stays when off.
6. **2 s check:** the live board delay timed on 10 real calls (event time vs browser time), written in the spec.

## Acceptance criteria
- [ ] pytest: off → 403; agent → 403; on → PBX called with listen-only; audit row for every result
- [ ] Real test: user listens to a test call between two office phones; neither side hears the supervisor
- [ ] Board delay ≤ 2 s on 10 calls (numbers written)

## Open questions
1. Who may listen: supervisor only (**recommended**) or admin too?
