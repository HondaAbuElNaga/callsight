# Shared calls tab + 24 h reply KPI (2026-11-22)

User's request: every call a supervisor shares with an agent ("المكالمات المشاركة مع الموظف") sits in one tab of the
agent's page; after reading, the agent closes it and it goes to the notes archive. Supervisor: same, for all agents.
The agent has 24 hours to reply; no reply in time lowers the agent's KPI.

Decided with the user:
1. The agent must **reply before closing**. Closing locks the reply.
2. The 24 h start when the supervisor **sends** the note (`created_at`).
3. Supervisor/admin: the same tab with every share and each reply; they close into **their own** archive
   (`staff_closed_at`, separate from the agent's `agent_closed_at`; since 0027 one archive per staff user, see the end). Closing by staff never changes the agent's KPI.
   The archive filters: من / إلى (day the note was sent, Riyadh) + الموظف (default: all agents).
4. KPI: new panel on the KPI page per agent: notes due, replied on time, late, no reply, % on time.
   A note counts once its 24 h ended or it was answered. Editing keeps the first reply time; removing the reply clears it.
   Only notes sent after migration 0026 ran count (`app_setting.share_kpi_since`). Page only, not in the PDF.

Built: migration 0026, `callsight/shares.py`, `GET /api/shares?box=open|archive&from&to&agent`,
`POST /api/shares/{id}/close`, `web/src/SharesTab.tsx`, KPI panel, tests `tests/test_shares_tab.py`.
Reviewed by 3 agents (bugs, security, accuracy): no high findings; fixes applied.

## Changes after the review (2026-11-22, user asked for points 2, 3, 5)
- **Reply rule:** a reply needs at least 10 characters (empty still removes it). An edit after the 24 h counts as a
  late reply (`first_reply_at` = the edit time); an edit within the 24 h, or resending the same text, keeps the time.
- **Own archive per staff user:** table `share_staff_close` (migration 0027). A supervisor's «نقل للأرشيف» moves the
  share only out of THEIR open list; other supervisors and the admin still see it open. `call_share.staff_closed_at`
  is no longer used; 0027 copied its closes to the staff user who made them (admin_log `share_close`).
- **Speed:** the list reads calls, names and own closes in a few queries, not one per share.
