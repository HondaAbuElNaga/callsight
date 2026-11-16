# Rating set: 500 calls from chosen days (2026-11-16, approved "ok approve")

User: "I need these days for التقييم: 1, 2, 3/10/2026, 20/10/2026, 20/9/2026, 1/11/2026 and some random days in
month 8 and 7; let it be 500 calls to finish our model."

The AWS server holds calls from 2026-11-15 only, so the days are fetched from the PBX first.

## Days (10)
2026-07-12, 2026-07-28, 2026-08-09, 2026-08-24 (random days of July / August), 2026-09-20, 2026-10-01, 2026-10-02,
2026-10-03, 2026-10-20, 2026-11-01.

## Requirements
1. Fetch: `reconcile --date D` for each day loads the PBX call list and downloads the recordings (server task
   button `rating-fetch`, value = the days).
2. Pick once (`rating-set create --days ... --n 500`, button `rating-create`): answered customer calls of team
   agents with a downloaded recording, talk 20 s .. ASR_MAX_SECONDS, not a branch supervisor's proof call, not a
   test call. 500 split evenly over the days (a small day gives its rest to the others); inside a day the agents take
   turns. Same days -> same calls. Stored in `rating_pick` (migration 0016); a second create is refused.
3. The picked calls get a transcript (queued at once; old days are before ASR_START_DATE). Their analysis (AI
   score) follows by the normal job when NLP_ENABLED=true.
4. Rating page order: the blind calls first, then the rating set (mixed days), then the newest calls.
5. The blind set (100, frozen once) is drawn from the rating set when one exists.
6. `rating-set status` (button `rating-status`): per day picked / transcribed / analysed / rated + GPU work left.

## Not in scope
No new page; nothing deleted; old days' calls also appear in the call list and KPIs of those days (they are real
calls). If the PBX no longer keeps recordings of a day, that day gives fewer calls and the others give more.
