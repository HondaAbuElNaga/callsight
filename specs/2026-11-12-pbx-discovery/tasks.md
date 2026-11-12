# Tasks: PBX discovery (Yeastar P-Series)

- [x] 1. User approves the spec and answers open questions
- [ ] 2. (User) Log into the Yeastar **admin** portal; screenshot: Dashboard (model + firmware) and the Plan/License page
- [x] 3. (User) Integrations → API: enable, add server IP to allow-list, copy Client ID/Secret into `.env`
- [ ] 4. (User) Call Features → Feature Code → Call Monitoring: give the supervisor extension **Listen** permission only
- [x] 5. (User) Check call recording is on for the agents' extensions/queue; note storage location
- [x] 6. Write `.env.example`, `.gitignore`, `requirements.txt`, `tools/pbx_probe.py` (token, cdr, recording, events)
         → done; .venv created, .env created from example
- [x] 7. Write unit tests for the probe (token refresh, CDR parsing, phone masking) with mocked responses
         → 12 tests, all pass
- [x] 7b. (User) Integrations → API → IP Restriction: allow office public IP `66.212.118.128/255.255.255.255` (temporary, via `sstli.ras.yeastar.com`)
         → first try 2026-11-12: errcode 70087 IP FORBIDDEN (PBX reached via Yeastar remote access)
         → public IP added by user, still 70087. Cause: FQDN remote API also needs System → Network → Yeastar FQDN → Remote Access → API Access enabled (+ IP allow-list). User has no System access → **blocked on supplier**. Stopped retrying (auto-block after repeated failed auth).
- [x] 7d. ~~Meanwhile (no API): user exports 7 days CDR (CSV) + ~10 recordings into `data/manual/` → inspect CDR fields + audio format; start ASR gold set~~ → not needed, LAN API works
- [x] 7c. (Supplier) Give access to System → Network / PBX local IP; then switch `.env` to `https://<pbx-lan-ip>:8088`, allow only the analytics PC/server LAN IP, remove the public-IP rule. Reason: stable (public IP may change), local-only traffic, faster recording downloads
- [x] 8. Run the probe against the PBX; save masked samples to `docs/samples/`
         → 2026-11-12 via LAN 192.168.50.151:8088: token OK, 3,906 CDR/7 days, recording = mono 8 kHz WAV. Events not yet run.
- [ ] 9. Test calls: 1 answered, 1 unanswered, 1 with supervisor `*51` listen; check CDR + events
- [~] 10. Fill `docs/pbx-discovery.md`; decide stereo vs diarization; finalise server specs
         → doc written; decision: mono → diarization (pyannote). Still open: agent mapping, events, server specs (560 calls/day whole PBX)
- [ ] 11. Verify the acceptance criteria
- [ ] 12. Review with 3 subagents (bugs, security, accuracy); fix confirmed findings
- [ ] 13. Spec status → done; tick the roadmap item; update "Current position"
