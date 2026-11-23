# Tasks: listen
- [x] 1. Step 8 done (announcement + legal OK, 2026-11-22). User approves (2026-11-23).
- [x] 2. **YOU:** PBX: «Allow Being Monitored» on 104/116/119; Feature Code Listen *51 permission 114 (done 2026-11-23).
- [x] 3. Branch `spec/2026-11-17-listen` (worktree). Tests first.
- [x] 4. Adapter `listen()` (call/listen type listen, sent once, 10 s, "unknown" kept apart), private `live:channels`, button, errors, `LISTEN_ENABLED`; ERP keys refused.
- [x] 5. Review; fixed. "ok push" → live with `LISTEN_ENABLED=false` (2026-11-23). Server tasks listen-on / listen-off.
- [ ] 6. User turns it on; real silent test; board delay on 10 calls.
- [ ] 7. Roadmap: Phase 5 items + "Done when".
