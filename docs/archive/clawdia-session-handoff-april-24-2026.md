# Clawdia Session Handoff — April 24, 2026

<!-- Migrated from Notion 34c2e075-ac64-817c-91f3-d13c289da6d4 on 2026-05-16. Archived session handoff or historical doc. -->

**Marathon session.** Started the day debugging a stale GOOGLE_SCOPES bug, ended with 48 live tools, every stability item closed, and email/calendar both dramatically better off.

## Current Status

- **Bot:** `/opt/clawdia/bot_new.py`, running via `systemctl status clawdia`, auto-restart on reboot
- **Tools loaded:** 48 (up from 34 this morning, +14 shipped today)
- **Model:** `claude-sonnet-4-6`
- **Health check:** PASSING at every restart today — Gmail personal/family, Calendar, OneNote, Notion, iCloud Mail, iCloud Calendar all green
- **Uptime:** clean since last restart at ~21:08 UTC
- **Logs:** `journalctl -u clawdia -f`

## Shipped Today (26 items)


### Stability & Infrastructure — 100% GREEN

- [x] **GOOGLE_SCOPES mismatch fix** — `drive.readonly` in code vs `drive` in token was causing refresh failures. Both tokens re-auth'd via `~/reauth_google.py`.
- [x] **Startup health check** — tests all integrations at boot, Telegram alert on failure. Already caught 2 regressions within seconds of deploy.
- [x] **Anti-fabrication system prompt** — stricter language preventing Clawdia from inventing tool-failure messages without calling the tool.
- [x] **Google/Calendar error classifier** — `_classify_google_error()` detects `invalid_scope`/`invalid_grant`/quota/403/429 and returns actionable `TOKEN_REFRESH_FAILED` instructions.
- [x] **iCloud app password expiry detection** — `_classify_icloud_error()` for IMAP + CalDAV auth failures. Health check probes iCloud too.
- [x] **RAM monitoring** — 15-min scheduler, Telegram alert at ≥80%, debounced recovery at 70%. Current baseline 27% of 2GB.
- [x] **Remote SSH via Desktop Commander** — replaces ttyd. Claude SSHes into VPS directly from Sean's Mac.
- [x] **Timezone bug in system prompt** — `build_system_prompt()` was UTC on server, labeled as Eastern. Fixed with `zoneinfo` + `%Z`.
- [x] **datetime.utcnow() deprecation cleanup** — 4 calls fixed, 1 downstream Google Calendar tz bug caught by the new health check.

### Email

- [x] **Oracle air-gap** — system prompt blocks all access to `1.Oracle` label. Work email stays out of Clawdia per Sean's clearance posture.
- [x] **Gmail mark as read** — `gmail_mark_read` supports personal and family via `account` param.
- [x] **Email threading** — `gmail_read_thread` reads full conversation, strips quoted reply tails, exposes ThreadID in `gmail_read` output.
- [x] **Outlook/Live email** — `outlook_mail_unread` / `outlook_mail_read` / `outlook_mail_send` via MS Graph (HTTPS, not SMTP — not blocked by DO). Required MS OAuth re-auth via new `~/reauth_ms.py` (MSAL device-code flow) to add `Mail.Send` + `Mail.ReadWrite` scopes.
- [ ] **iCloud Mail send (SMTP)** — DO ticket **#12078131** filed 2026-04-24 (Account → SMTP → Port-25/SMTP Block). DO Security team asked 3 clarifying questions, replied with full details on transactional-only, <20/mo, authenticated relay through Apple, no API alt. Awaiting decision. **Follow up by 2026-04-27.** If approved, re-apply icloud_mail_send patch from `/opt/clawdia/bot_new.py.bak-*` around 19:40 UTC 2026-04-24.

### Calendar

- [x] **`calendar_add_event`**** all-day support** — auto-detects `YYYY-MM-DD` strings, uses Google `date` payload.
- [x] **Cross-calendar conflict detection** — `check_availability(start, end, buffer_minutes=15)` queries Google + iCloud simultaneously. Handles all-day events. Timezone-correct (converts to UTC before strftime). Returns `BUSY` / `FREE` / `TIGHT` with full conflict list.
- [x] **Calendar data populated** — 7 events added (Ford service [since deleted, passed], Hailey Tea Party, TurboTax, Graduation, World Cup, NH trip, Military Camp pickup).
- [ ] **iCloud Calendar event creation** — CalDAV `PUT` with iCal format, not yet built
- [ ] **iCloud Calendar event deletion** — CalDAV `DELETE` by event UID, not yet built

### Intelligence & Automation

- [x] **Task pause/resume** — new `paused` column on `scheduled_tasks`, `/task pause <id>` / `/task resume <id>`, `[PAUSED]` marker in `/task list`. Migrated existing DB in place.
- [x] **Telegram photo/file handling** — Documents already worked (.txt/.docx/.pdf/.csv/.ics). NEW: `handle_photo` downloads highest-res, base64-encodes, sends to Sonnet 4.6 vision. Replies chunked for Telegram's 4000-char limit. Caption optional (default: "What is in this image?"). Tested end-to-end.
- [ ] Smarter morning briefing — prioritize unread by sender importance
- [ ] Multi-step workflows — e.g. "every Friday, summarize Oracle emails and save to OneNote" (blocked by air-gap for Oracle, but pattern is valid for non-work)
- [ ] Proactive calendar nudges — unblocked by today's `check_availability`

### Notion Integration

- [x] **5 read/write tools** — `notion_search`, `notion_read`, `notion_append_bullet`, `notion_create_page`, `notion_query_database`
- [x] **3 edit/delete tools** — `notion_list_blocks`, `notion_delete_block`, `notion_update_block`. Known limitation: update loses bold/italic (replaces rich_text with plain text).

## Tool Count Progression

[table - rendering not yet implemented]
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 

## Follow-Ups (Priority Order)

1. **2026-04-27:** Check on DO ticket #12078131 if no response by then.
1. **If DO approves:** Re-apply icloud_mail_send from backup `/opt/clawdia/bot_new.py.bak-20260424-*` ~19:40 UTC.
1. **Next session:** Proactive calendar nudges (uses `check_availability` we just built).
1. **Later:** iCloud Calendar CRUD, smarter morning briefing, Telegram voice transcription.

## Key Learnings This Session

- **Scope mismatch = silent death.** Always verify `GOOGLE_SCOPES` / `MS_SCOPES` in code match what the token holds. A narrower code scope rejects wider-scope refreshes.
- **Clawdia can fabricate tool-failure messages** when she decides not to call a tool. Mitigated by strict system prompt language.
- **`strftime('%Y-%m-%dT%H:%M:%SZ')`**** strips tzinfo without converting.** Must `.astimezone(timezone.utc)` first. Classic bug, caught in `check_availability` by direct testing.
- **Salesforce Lightning combobox can't be JS-driven.** Sean clicks manually, Claude drafts content.
- **Health check caught 2 regressions in seconds.** Design value proven — this pattern is worth repeating for future integrations.
- **DO's port 587 block is across the board.** Consumer VPS = SMTP off by default. Use MS Graph or Gmail API (both HTTPS) for anything critical. iCloud SMTP is the one hole.

## Critical Files & Locations

[table - rendering not yet implemented]
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 

## Backups Generated Today

All under `/opt/clawdia/bot_new.py.bak-20260424-*` — ~15+ backups, one per patch. Also `tasks.py.bak-*` and `briefing.py.bak-*` from relevant patches.