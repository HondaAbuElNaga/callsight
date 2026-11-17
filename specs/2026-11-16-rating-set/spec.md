# Rating set: 500 calls from chosen days (2026-11-16, approved "ok approve")

**Status:** done, live 2026-11-16 (500 picked; the user rates them next)

User: "I need to this day to make التقيم so 1, 2, 3/10/2026 and 20/10/2026 and 20/9/2026 and 1/11/2026 and some
random day in month 8 and 7; let it 500 call to finish our model." (2 random days per month = our choice.)

The AWS server holds calls from 2026-11-15 only, so the days are fetched from the PBX first.

## Days (10)
2026-07-12, 2026-07-28, 2026-08-09, 2026-08-24 (random days of July / August), 2026-09-20, 2026-10-01, 2026-10-02,
2026-10-03, 2026-10-20, 2026-11-01.

## Requirements
1. Fetch (`rating-set fetch --days ...`, button `rating-fetch`): ONE walk of the PBX call list over the days; the
   team calls are stored, their recordings marked `held` (left on the PBX: never downloaded unless picked).
2. Pick once (`rating-set create --days ... --n 500`, button `rating-create`): answered customer calls of team
   agents with a downloaded recording, talk 20 s .. ASR_MAX_SECONDS, not a branch supervisor's proof call, not a
   test call (talk more than 20 s, recording file on the PBX). Only the picked recordings are downloaded; a
   failed download is replaced by the next call of the same day. 500 split evenly over the days (a small day gives its rest to the others); inside a day the agents take
   turns. Same days -> same calls. Stored in `rating_pick` (migration 0016); a second create is refused.
3. The picked calls get a transcript (queued at once; old days are before ASR_START_DATE). Their analysis (AI
   score) follows by the normal job when NLP_ENABLED=true.
4. Rating page order: the blind calls first, then the rating set (mixed days), then the newest calls.
5. The blind set (100, frozen once) is drawn from the rating set when one exists.
6. `rating-set status` (button `rating-status`): per day picked / transcribed / analysed / rated + GPU work left.

## Review notes (3 agents, 2026-11-16)
- Only the 500 picked recordings are downloaded (privacy: no whole days on AWS; no 2-hour job; the 2-minute
  download job skips `held`). Caller numbers are never written to the logs (recording file name).
- The fetch is one process: the first PBX error stops it (no login storm).
- The blind set freezes only when every picked call has finished its transcript (then 100 of the whole set).
  If it was already frozen from other calls before the set is made, `rating-create` warns (it never changes).
- AI score needs `NLP_ENABLED=true` on the main server and the GPU server: turned on AFTER `rating-create`.
- 2026-10-02 (Friday) / 10-03 (Saturday) are weekend days: few calls; the other days fill the 500.
- Talk must be more than 20 s. Failed transcripts of picks are retried by `rating-set queue`.
- Retention is off today. When it is turned on, its days must be larger than the age of the oldest picked day
  (July 12), or the ratings and transcripts of old picks are erased: turn it on only after training.
- The 500 old calls are queued at once: live calls wait behind them on the GPU for one evening (accepted).

## Order after deploy
1. `rating-status` (blind set not frozen? NLP flag). 2. `rating-fetch` with the 10 days. 3. `rating-create` with
the same days (downloads the 500 recordings). 4. NLP on (main + GPU). 5. Wait for the GPU (08:00–23:30), check `rating-status`. 6. Rate.

## Not in scope
No new page; nothing deleted; old days' calls also appear in the call list and KPIs of those days (they are real
calls). If the PBX no longer keeps recordings of a day, that day gives fewer calls and the others give more.
