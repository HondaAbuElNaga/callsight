# PBX discovery: Yeastar P-Series

_Last updated: 2026-11-12_

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
- Open: how a call that passes IVR → ring group/queue → agent appears (one row or several rows); check with `cdr/detail` (CDR 2.0) on a known test call.

## Recordings: `recording/list` + `recording/download`
- Format: **WAV, 1 channel (mono), 8000 Hz, 16-bit**, both voices mixed.
- → Phase 2 needs **speaker diarization** (agent vs customer). Stereo is not available here.
- File name pattern: `<datetime>-<timestamp>.<seq>-<ext>-<number>-<Outbound|Inbound>.wav`.

## Live events / Listen
- WebSocket events: not tested yet (`pbx_probe.py events` during a test call).
- Listen `*51`: waiting for supplier (Feature Code permission).

## Agent mapping (to confirm with user)
Extension names seen: خدمة العملاء, متابعه المبيعات 116, المبيعات, خدمة العملاء محمود هشام, محمود مكي مبيعات, د. حسام مشرف (503), 400, 505, 600 …
