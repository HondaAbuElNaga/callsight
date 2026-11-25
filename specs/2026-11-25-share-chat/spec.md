# Shared calls become a popup chat (2026-11-25)

User's request: the tab «المكالمات المشاركة مع الموظفين» / «ملاحظات المشرف» on the follow-ups page goes away. When a
supervisor shares a call with an agent, it reaches the agent as a **popup chat** on the agent's page. After the agent
answers, the supervisor can **close** the chat or **archive** it.

Decided with the user (2026-11-25):
1. **Real chat:** many messages back and forth on one shared call (one share = one chat, its first message is the
   supervisor's note).
2. **Close** (staff): the chat disappears for the agent and for every supervisor/admin, and can't be reopened. It
   still counts in the KPI.
3. **Archive** (staff): the chat leaves both sides' open lists and goes to the archive (staff only, filtered by
   من/إلى = the day it was shared + الموظف). A supervisor can **reopen** an archived chat later (the reopen button, or by
   writing a new message in it); it then pops up for the agent again.
4. **Supervisor:** a popup opens when an agent writes, and a chat button (on every page) lists open chats + archive.
5. **24 h KPI kept:** the agent's first message of at least 10 characters, within 24 h of the share being sent, is
   an on-time reply. Messages can't be edited or removed, so the first-reply time never moves.

Notes:
- Agents can't close or archive; they only read and write. They never see closed or archived chats.
- Unread: each side has a read mark (`agent_read_at`, `staff_read_at`, shared by all staff). Opening a chat marks it read
  (and, the first time the agent opens it, sets `seen_at` = "opened" as before).
- The page asks for the open-chats list every 20 s while visible; a chat with unread messages from the other side
  pops up by itself.
- Retention keeps a call's content while its chat is open or archived (closed chats no longer hold it).
- Migration 0031: `call_share.status` (open/archived/closed), `archived_at`, `closed_at`, `agent_read_at`,
  `staff_read_at`, table `share_message`. Existing notes and replies become messages. Shares the agent had closed
  become archived; staff read marks start at the migration time, so old replies don't pop up.
  `agent_closed_at`, `staff_closed_at` and `share_staff_close` stay in the DB but are no longer used.

API: `GET /api/chats?box=open|archive&from&to&agent`, `GET /api/chats/{id}` (marks read),
`POST /api/chats/{id}/messages`, `POST /api/chats/{id}/archive|close|reopen` (staff). Kept: `POST/GET
/api/calls/{id}/shares` (start a chat / chats of a call), `DELETE /api/shares/{id}`. Removed: `GET /api/shares`,
`POST /api/shares/{id}/seen|close`, `PUT /api/shares/{id}/reply`.
