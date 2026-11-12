# PBX discovery: Yeastar P-Series

_Last updated: 2026-11-12 (evening)_

## Connection
| Item | Value |
|---|---|
| PBX | Yeastar P-Series, LAN `192.168.50.151`, API on HTTPS port **8088** (self-signed cert) |
| Remote address | `sstli.ras.yeastar.com` (remote API **not** enabled; we don't need it) |
| API | Enabled; IP Restriction allows only the analytics PC `192.168.50.189` (public-IP rule removed 2026-11-12) |
| Login | `get_token` OK, token 1800 s, refresh OK |
| Plan / firmware | Unknown (no Dashboard access); API works, so the plan includes the API |

## CDR (call records): `cdr/list`
- Volume: **3,906 calls in 7 days** for the whole PBX (≈ 560/day): Inbound 2,487 · Outbound 976 · Internal 443.
- Dispositions: ANSWERED 2,569 · NO ANSWER 1,008 · VOICEMAIL 209 · BUSY 120.
- Fields: `id, time, timestamp, uid, call_from, call_from_number, call_from_name, call_to, call_to_number, call_to_name, src_addr, src_trunk, dst_trunk, duration, ring_duration, talk_duration, disposition, call_type, did_number, dod_number, record_file, reason`.
  - `talk_duration` and `record_file` are present only when answered/recorded.
  - Time format: `MM/DD/YYYY hh:mm:ss AM/PM` (PBX local time); `timestamp` = Unix seconds.
- Inbound calls enter **IVR 6200** first. 965 inbound rows end at the IVR (disposition ANSWERED by the IVR, no recording). **The KPIs must not count an IVR "ANSWERED" as answered by an agent.**
- All other answered calls have a `record_file` (1,578 recorded of 2,569 answered; the gap ≈ the IVR-only calls).
- **One call = several CDR rows with the same `uid`** (checked 2026-11-12 on 1,000 rows; `cdr/detail` does not exist here, errcode 10001):
  | Shape (rows in id order) | Meaning |
  |---|---|
  | IVR ANSWERED → agent ext ANSWERED → Queue ANSWERED | answered by agent via queue; agent row `ring_duration` = queue wait |
  | IVR ANSWERED → Queue NO ANSWER | caller hung up waiting in queue (Queue row `duration` = wait) |
  | IVR ANSWERED only | caller hung up in the IVR menu |
  | ext NO ANSWER/BUSY → Voicemail | direct call to an extension went to voicemail |
  | … → agent ANSWERED → other ext ANSWERED (`attended_transfer_answer`) | transferred call |
- Extensions appear as `Name<ext>` in `call_to`; dialled codes (e.g. `*51119` → `51119`) appear without brackets.
- No hold time in the CDR; for ring-all queues no row for agents who rang but didn't answer.
- Queues (`queue/list`): **6401 خدمة العملاء** = ext 104, 119 (ring all); **6400 المبيعات** = 19 agents.
- API limit: **max 8 valid tokens** per API app (30 min each) → errcode 60002 "MAX LIMITATION EXCEEDED"; reuse/refresh one token.
- `recording/download?file=<record_file>` works (no need to look up the recording id).

## Recordings: `recording/list` + `recording/download`
- Format: **WAV, 1 channel (mono), 8000 Hz, 16-bit**, both voices mixed.
- → Phase 2 needs **speaker diarization** (agent vs customer). Stereo is not available here.
- File name pattern: `<datetime>-<timestamp>.<seq>-<ext>-<number>-<Outbound|Inbound>.wav`.

## Live events / Listen
- WebSocket events: not tested yet (`pbx_probe.py events` during a test call).
- Listen `*51`: waiting for supplier (Feature Code permission).

## Agent mapping (to confirm with user)
Extension names seen: خدمة العملاء, متابعه المبيعات 116, المبيعات, خدمة العملاء محمود هشام, محمود مكي مبيعات, د. حسام مشرف (503), 400, 505, 600 …
