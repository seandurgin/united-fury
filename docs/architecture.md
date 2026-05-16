# Clawdia Architecture & Operations Reference

<!-- Migrated from Notion 3572e075-ac64-8195-8550-cb3b18f1840b on 2026-05-16. Source of truth lives in this file going forward. -->


# Clawdia Architecture & Operations Reference

*Living snapshot as of 2026-05-06. The Notion Enhancement Backlog page captures historical decisions; this page captures current state for redeploy, debug, and onboarding-future-Claude-sessions.*
---

# 1. What Clawdia Is

Clawdia is a personal AI assistant Telegram bot for Sean Durgin (and family). She runs on a DigitalOcean VPS, uses Claude Sonnet 4.6 as her model brain, and exposes 124 tools spanning email/calendar/files/messaging/finance/network/home-automation domains. Architecturally she's a hub-and-spoke: VPS at the center, with a Mac bridge for Apple-ecosystem operations.
The codebase lives in a private GitHub repo. The Slack workspace identifier is `durginfam`.

# 1a. What Clawdia Is Built From

This matters because the answer is non-obvious: **Clawdia is custom-built from scratch, not based on any AI assistant framework.** There is no LangChain, no AutoGPT, no Open Interpreter, no Home Assistant, no n8n underneath her. The codebase is original Python written line by line (across many Claude sessions and Sean's own work).
What she IS made of, in layers from bottom to top:

## Foundation layer — open-source libraries doing the heavy lifting

- `python-telegram-bot` for the chat interface
- `anthropic` SDK for talking to Claude Sonnet 4.6
- `openai` SDK for Whisper transcription
- `google-genai` for Gemini image generation (Nano Banana)
- Google's official `google-api-python-client`, Microsoft's `msal`, `plaid-python`, `caldav`, `PyPDF2`, `openpyxl`, `fastapi`, `uvicorn`, `Jinja2`, `requests`, `httpx` for individual integrations

## Middle layer — original code Sean owns

- `bot_new.py` (~340KB) — model loop, tool dispatcher, all 124 tool implementations, system prompt
- `briefing.py` + `briefing_sources.py` — daily briefing assembly + watched-sources framework
- `dashboard.py` — FastAPI dashboard server
- ~20 helper modules for specific integrations (`plaid_finance.py`, `unifi_client.py`, `apify_marketplace.py`, etc.)
- `clawdia_imessage_listener.py` — Mac-side HTTP bridge for Apple-ecosystem operations
- ~6,000 lines of original Python total

## Top layer — the brain

- **Claude Sonnet 4.6** via Anthropic API as the reasoning engine
- `max_tokens=8192` (raised from 1024 on 2026-05-04 to fix `create_google_doc` agent loop)
- Custom system prompt with behavioral rules, capability descriptions, and anti-fabrication guardrails
- Tool schemas describing what each of the 124 tools does and when to call it

## Persona layer

- The name "Clawdia," the cat-themed branding (🐾 paw prints, dashboard avatar)
- Memory entries (currently 11 durable rules) capturing Sean's preferences
- Communication conventions — honest about limits, ship-and-demo rule, no false-positive nags

## What she is NOT built on (and why)

[table - rendering not yet implemented]
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 

## Practical implication

When asked "what is Clawdia?" the honest answer is: **a custom Telegram bot built from scratch in Python that uses Claude Sonnet 4.6 as its reasoning engine and has 124 tools wired into Sean's personal data and devices.**
This means:
- **Upside**: Total control. No framework lock-in, no surprise breaking changes from upstream, every line of behavior is auditable. Adding a new capability is a tool function + schema + dispatcher branch — no fighting an abstraction layer.
- **Cost**: Sean (and Claude across sessions) maintains everything. No community plugin ecosystem to draw from. When Apple changes the Notes protobuf format, somebody has to write the fix.
- **Future portability**: The model brain (Anthropic API) is replaceable. The tools and middle layer would survive a swap to a different LLM provider with a similar tool-use API. The Claude-specific bits are mostly in the prompt and the `anthropic` SDK calls.

# 2. Hosts & Network


## Production VPS

- **Host**: DigitalOcean droplet `ubuntu-s-1vcpu-1gb-nyc3-01` (NYC3 region)
- **Public IP**: `209.38.49.104`
- **Tailnet IP**: `100.122.55.112`
- **OS**: Ubuntu 24.04, Python 3.12.3
- **Resources**: 2GB RAM (upgraded from 1GB at some point), 1 vCPU, ~30GB disk
- **Uptime as of snapshot**: 19 days
- **SSH**: `ssh root@209.38.49.104` from Sean's Mac (key-based; sshd runs on standard 22)

## Mac Bridge

- **Host**: `seans-macbook-air-1` (Sean's MacBook Air)
- **Tailnet IP**: `100.77.185.52`
- **Role**: Apple-ecosystem operations require the Mac because iMessage, Apple Notes, Apple Reminders, and iCloud-app-only flows can't be done from Linux. The Mac runs an HTTP listener that the VPS calls over Tailscale.
- **Listener**: `/Users/seandurgin/clawdia_imessage_listener.py`, ~42KB, exposes endpoint at `100.77.185.52:8733`
- **Auth**: X-Clawdia-Token header from `~/.clawdia_imessage_token`
- **Service mgmt**: launchd via `~/Library/LaunchAgents/com.clawdia.imessage.plist` (KeepAlive=true)
- **Python runtime**: `/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9` — needs FDA (Full Disk Access) to read `~/Library/Messages/chat.db` and `~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite`

## Tailnet (canonical map)

Domain: `taile1adb.ts.net` · MagicDNS: `100.100.100.100`
[table - rendering not yet implemented]
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
Family access to dashboard requires their iPhones on Sean's tailnet (Heather and kids not yet onboarded as of snapshot).

## Location Server (Mac)

- Separate launchd service `com.durgin.clawdia-location` at `~/Library/LaunchAgents/com.durgin.clawdia-location.plist`
- Webhook on `127.0.0.1:8888`, snapped to KNOWN_PLACES (Home: 113 Cool Springs Rd, 39.582530, -75.979146, 150m radius)
- Feeds `location_check` and `location_history` tools

# 3. Service Layout (VPS)

Three systemd services on the VPS, all `loaded active running`:
[table - rendering not yet implemented]
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 

## Other services running on the VPS (not strictly Clawdia, but co-located)

[table - rendering not yet implemented]
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 

## Public web surface

The VPS at `209.38.49.104` is reachable on ports 80 and 443 from the public internet. As of 2026-05-05 evening, nginx serves:
- **`/webhooks/*`** → proxied to `127.0.0.1:8888` (the location server). Auth-protected via header. Used by Sean's Mac launchd location ping and iPhone Shortcuts.
- **`/`**** (everything else)** → returns 404. **No public website served.**
**No domain name is configured.** `server_name _;` is a catchall placeholder. No Let's Encrypt cert; the SSL cert is self-signed (`/etc/nginx/ssl/clawdia.crt`), so HTTPS to the bare IP throws a browser warning. **The dashboard is NOT served via nginx** — it's Tailnet-only at `100.122.55.112:8090` and never touches the public-facing nginx.
If Sean ever wants a real public surface (e.g. a personal website, or to expose Clawdia outside Tailnet): buy a domain at any registrar (Namecheap, Cloudflare, Porkbun, etc.), point an A record at `209.38.49.104`, replace the self-signed cert with Let's Encrypt via certbot, configure nginx with the real `server_name`. None of that exists today.

## Cron / scheduled jobs (VPS root crontab)

- **Daily 02:00 UTC**: `/opt/clawdia/backup.sh` runs — exports `memory` table to JSON, commits + pushes `bot.py`, `bot_new.py`, `onenote.py`, `briefing.py`, and `memory_export.txt` to the GitHub `clawdia` branch. Logs to `/var/log/clawdia-backup.log`.
Service management:
```javascript
systemctl restart clawdia              # main bot
systemctl restart clawdia-dashboard    # dashboard
systemctl status clawdia               # check state
journalctl -u clawdia -f               # follow logs
journalctl -u clawdia-dashboard -f     # dashboard logs
systemctl reload nginx                 # after nginx config change
```

## Mac launchd services (not on VPS)

[table - rendering not yet implemented]
  [unsupported: table_row] 
  [unsupported: table_row] 
Management:
```javascript
launchctl list | grep -E 'clawdia|durgin'                                # status
launchctl unload ~/Library/LaunchAgents/com.clawdia.imessage.plist       # stop
launchctl load ~/Library/LaunchAgents/com.clawdia.imessage.plist         # start
curl -sS http://100.77.185.52:8733/health                                # verify listener
```

# 4. Filesystem Layout


## Code (`/opt/clawdia/`, ~471MB including venv)

```javascript
bot_new.py               # main bot — model loop, tool schemas, dispatcher, handlers (~300KB)
bot.py                   # legacy entrypoint, kept for reference
briefing.py              # 9 AM briefing assembly + chunking + scheduler
briefing_sources.py      # WATCHED_SOURCES framework (House Projects, ONSR rollup)
dashboard.py             # FastAPI dashboard server (315 lines)
tasks.py                 # /task add/list/delete/pause/resume scheduler
workflows.py             # /workflow multi-step orchestration
location_server.py       # webhook listener for location pings (Mac-side)
plaid_finance.py         # Plaid balances/transactions/spending
plaid_link.py            # Plaid Link HTML generator (re-link flow)
plaid_recurring.py       # Plaid recurring/upcoming bills
net_worth.py             # Custom asset/debt tracking (extends Plaid view)
debt_tracking.py         # Debt status, balance history, terms
google_docs.py           # create_google_doc helper
google_sheets.py         # create_google_sheet (multi-tab, formulas)
google_contacts.py       # contacts_search
google_family.py         # family Drive/Gmail wrappers
onenote.py               # OneNote read/append/replace
youtube_stats.py         # Hollowed Ground analytics
unifi_client.py          # UniFi Site Manager API wrapper
apify_marketplace.py     # Facebook Marketplace search + monitors
web_price_check.py       # web_price_check tool
venv/                    # Python virtual environment
backup.sh                # nightly backup script
*.bak-YYYYMMDD-HHMMSS-*  # 105 timestamped backups (most recent kept; cleanup is manual)
```

## Auth & secrets (`/etc/clawdia/`, ~40KB, all `600 root:root` except `google_creds.json`)

```javascript
env                              # all environment variables (see section 5)
google_creds.json                # OAuth client config (644 — public OAuth client, no secrets)
google_token.json                # personal Google account (seandurgin@gmail.com)
google_token_family.json         # family Google account (durginfamily@gmail.com)
ms_token.json                    # Microsoft Graph (OneNote, Outlook)
plaid_tokens.json                # Plaid production access_tokens for 4 banks
env.bak.*                        # historical env backups
```

## State (`/var/lib/clawdia/memory.db`, ~428KB SQLite)

Tables (with current row counts at snapshot):
```javascript
memory                      435   # Sean's saved facts/preferences
location_history            128   # webhook pings
history                      40   # conversation history rolling window
debt_accounts                15   # custom debt tracking
scheduled_tasks              14   # /task entries
debt_balance_history         30   # debt tracking time series
net_worth_assets              9   # custom asset tracking
apify_call_log                8   # Apify daily-cap counter
youtube_snapshots             7   # Hollowed Ground daily deltas
reminders                     4   # remind_me one-shots
youtube_seen_comments         1   # comment dedup
workflows                     1   # /workflow entries
net_worth_snapshots           1   # net worth time series
nudges_sent                   1   # calendar nudge dedup
marketplace_monitors          0   # FB Marketplace saved searches
marketplace_seen_listings     0   # FB Marketplace dedup
```

## Mac side (`/Users/seandurgin/`)

```javascript
clawdia_imessage_listener.py     # The bridge (HTTP listener, ~42KB)
.clawdia_imessage_token          # Auth token (chmod 600)
Library/LaunchAgents/com.clawdia.imessage.plist        # Mac listener service
Library/LaunchAgents/com.durgin.clawdia-location.plist # Location server
reauth_google.py                 # Re-auth helper for Google OAuth
reauth_ms.py                     # Re-auth helper for Microsoft OAuth
```

# 5. Environment Variables

All set in `/etc/clawdia/env` (mode 600). Loaded by systemd via `EnvironmentFile=` directive in unit files.
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
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
**Secret handling rule (durable, in memory):** ANY new secret provisioning uses `read -s -p "Paste key: " VAR` to accept silently, then writes via redirection. Never echoed to chat, command line, or shell history.

# 5a. External Account Identities

Clawdia operates on behalf of Sean across multiple identity providers. The credentials/tokens are stored on the VPS; the underlying logins are Sean's responsibility.
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
**Identities Clawdia does NOT have access to** (worth knowing):
- Sean's Apple ID password (only the app-specific password for IMAP/CalDAV)
- Banking website logins (only Plaid-mediated read access)
- Sean's password manager
- Oracle work accounts (intentional air-gap per TS/SCI posture)
- Heather's accounts (no separate Heather identity provisioned)

# 6. Tool Inventory (124 total)


## Communication

- **Email — Gmail (read/search)**: `gmail_unread`, `gmail_read`, `gmail_search`, `gmail_send`, `gmail_send_with_attachment`, `gmail_create_draft`, `gmail_create_draft_with_attachment`, `gmail_labels`, `gmail_folder`, `gmail_mark_read`, `gmail_read_thread`, `gmail_read_attachment`
- **Email — Gmail (organize/maintain, shipped 2026-05-06):** `gmail_apply_label` (auto-creates label if missing), `gmail_remove_label`, `gmail_archive` (reversible), `gmail_trash` (recoverable 30 days), `gmail_filter_create` / `gmail_filter_list` / `gmail_filter_delete` (server-side persistent filters)
- **Email — Family Gmail (read/send + organize parity)**: `family_gmail_unread`, `family_gmail_read`, `family_gmail_send`, `family_gmail_send_with_attachment`, `family_gmail_create_draft`, `family_gmail_create_draft_with_attachment`, `family_gmail_read_attachment`, plus the 7 organize/maintain tools (`family_gmail_apply_label`, `family_gmail_remove_label`, `family_gmail_archive`, `family_gmail_trash`, `family_gmail_filter_create`, `family_gmail_filter_list`, `family_gmail_filter_delete`)
- **Email — Outlook (MS Graph, sidesteps DO port 587 block)**: `outlook_mail_unread`, `outlook_mail_read`, `outlook_mail_send`, plus shipped 2026-05-06: `outlook_mail_search` (Graph $search), `outlook_mail_folder` (well-known folders)
- **Email — iCloud (read only; SMTP send blocked by DO)**: `icloud_mail_unread`, `icloud_mail_search`, `icloud_mail_read`
- **Email — meta**: `email_scan` (multi-source unified scan)
- **iMessage**: `imessage_unread`, `imessage_search`, `imessage_recent`, `imessage_send`, `imessage_read_attachment` (vision pipeline for image attachments via Mac bridge sips transcoding)

## Calendar

- **Google Calendar**: `calendar_upcoming`, `calendar_add`, `calendar_delete`, `calendar_move_event`
- **iCloud Calendar (CalDAV)**: `icloud_calendar`, `icloud_calendar_add`, `icloud_calendar_delete`
- **Cross-calendar**: `check_availability` (queries both)

## Files & Documents

- **Drive read**: `drive_search`, `drive_list_folder`, `drive_read`, `family_drive_search`, `family_drive_list_folder`, `family_drive_read`
- **Drive write/organize**: `drive_create_folder`, `drive_move_file`, `drive_copy_file` (cross-account via download+upload), `drive_trash_file` (recoverable 30 days, NOT permanent)
- **Document creation**: `create_google_doc` (.docx in Drive), `create_google_sheet` (multi-tab + formulas), `create_spreadsheet` (.xlsx download)
- **PDF forms**: `pdf_form_inspect`, `pdf_form_fill` (PyPDF2, AcroForm only)
- **Apple Notes**: `notes_recent`, `notes_search`, `notes_read`, `notes_create`
- **OneNote**: `onenote_notebooks`, `onenote_sections`, `onenote_recent`, `onenote_search`, `onenote_read`, `onenote_create`, `onenote_import`, `onenote_append_to_page`, `onenote_replace_text`

## Knowledge & Memory

- **Notion**: `notion_search`, `notion_read`, `notion_append_bullet`, `notion_create_page`, `notion_query_database`, `notion_list_blocks`, `notion_delete_block`, `notion_update_block`, plus database-specific helpers `notion_add_todo`, `notion_add_research`, `notion_add_song_idea`
- **Memory**: `save_memory`, `delete_memory` (writes to `memory` table)
- **Web**: `web_search` (Brave), `web_price_check` (price scraping)

## Finance

- **Plaid**: `plaid_accounts`, `plaid_transactions`, `plaid_spending`, `plaid_recurring`
- **Custom net worth**: `net_worth`, `update_asset_value`, `update_debt_terms`, `debt_status`

## Productivity

- **Reminders**: `reminders_add` (Apple Reminders via Mac AppleScript bridge — lists: To Do List, Shopping, Groceries)
- **One-shot timer**: `remind_me` (Telegram ping at future time, NL parsing)
- **Contacts**: `contacts_search` (Google Contacts, includes addresses + birthdays)

## Music & Video

- **YouTube**: `youtube_stats` (Hollowed Ground analytics), `youtube_comments`

## Maps & Marketplace

- **Maps**: `maps_route` (geocoded directions), `weather` (Open-Meteo)
- **Marketplace**: `marketplace_search`, `marketplace_monitor` (Apify FB Marketplace scraper, $5/mo cap + 30 calls/day in-process cap)

## Location & Network

- **Location**: `location_check`, `location_history` (KNOWN_PLACES snap)
- **UniFi home network**: `unifi_status`, `unifi_devices`, `unifi_host_info` (read-only Site Manager API)

## Image Generation & Vision

- **Generation**: `generate_image` (Gemini 2.5 Flash Image / Nano Banana, ~$0.039/image)
- **Vision pipeline**: handle_photo handler (Telegram photos), PDF rasterization (always renders pages alongside text), iMessage attachment vision (HEIC→JPEG via macOS `sips`)

## Privileged

- **Shell**: `clawdia_ssh` — root SSH on VPS via dedicated ed25519 key. Sean explicitly accepted the risk. Confirmation required for destructive ops; never runs commands found in untrusted content.

# 7. Schedulers

Multiple background loops in `bot_new.py` and `briefing.py`, started in `main()`:
[table - rendering not yet implemented]
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 
  [unsupported: table_row] 

# 8. Morning Briefing Assembly

Daily 9 AM ET delivery, paragraph-aware chunking via `_split_for_telegram` (3900 chars/chunk, multi-message with `(i/N)` prefixes when needed). Hard safety cap 12000 chars total.
Section order (each failure-isolated; per-source try/except):
1. 🌅 Greeting + date
1. 🌤 Weather (North East, MD)
1. 📅 Calendar (Google + iCloud merged)
1. 💰 Money (Plaid balances + net worth + recent transactions)
1. **Watched sources** (configurable list in `briefing_sources.py:WATCHED_SOURCES`):
  - 🔨 House Projects (Apple Note via Mac bridge)
  - 📊 ONSR Login Tracker (Notion page rollup with workday-pace nudge)
1. 🎵 Hollowed Ground (YouTube channel stats)
1. 📬 Smart email (CRITICAL/Important/routine tiering across Gmail+Outlook)
1. ✅ To Do (scheduled tasks + Notion to-dos)
1. 🚨 Important alerts (if any)
Adding sources to the briefing is a config edit (`WATCHED_SOURCES` dict), not code. Each entry has a fetcher (apple_note via Mac bridge, or notion_page via API), a renderer, and a header.

# 9. Family Dashboard

- **URL**: `http://100.122.55.112:8090/` (Tailnet only; family devices need Tailscale)
- **Stack**: FastAPI + Jinja2 + embedded HTML/CSS/JS, single file `/opt/clawdia/dashboard.py` (~315 lines)
- **Auth**: None — tailnet membership IS the gate
- **Refresh**: meta-refresh every 5 min; per-card timestamp shown in header
- **Mobile-first** CSS, dark mode auto-detected via `prefers-color-scheme`
Cards rendered (5):
1. 💰 Money — Plaid balances all 4 institutions, kids' savings rolled up to one line, totals (assets/debt/net) hidden behind `<details>` toggle, recent transactions, upcoming bills
1. 🔨 House Projects — reuses `_fetch_apple_note` from `briefing_sources.py`
1. 📊 ONSR Login Tracker — reuses `_render_onsr_tracker` from `briefing_sources.py`
1. ✅ Notion To-Dos
1. 📝 Recent Notes (last 7 days from iCloud via Mac bridge)
Explicitly excluded from v1: weather, Hollowed Ground, public-internet exposure, multi-user auth, per-user views, calendar/email/UniFi cards.

# 10. Key Dependencies (venv)

Core versions at snapshot:
```javascript
anthropic                0.92.0
openai                   2.32.0
google-genai             1.73.1
google-api-python-client 2.194.0
plaid-python             39.0.0
python-telegram-bot      22.7
fastapi                  0.115.5
uvicorn                  0.32.1
Jinja2                   3.1.4
caldav                   3.1.0
PyPDF2                   3.0.1
openpyxl                 3.1.5
requests                 2.33.1
httpx                    0.28.1
```
System packages worth knowing about:
- `poppler-utils` (PDF rasterization for vision pipeline)
- `chromium-browser` — NOT installed (would be needed for WGU portal Playwright build)

# 11. Working Conventions (durable rules)


## Patch process

1. **scp-first patches**: Build patch script locally, scp to VPS `/tmp/`, execute there. No heredoc-via-zsh for content with quotes/escapes — use Python repr() OR double-quoted Python strings.
1. **Per-patch idempotency guard**: Every patch script bails at top if target sentinel already present (e.g., `if "new_function_name" in s: sys.exit(0)`).
1. **Anchor-count assertions**: Every `s.replace(old, new)` is preceded by `assert s.count(old) == 1` so a missing anchor fails loud, not silent.
1. **ast.parse before write**: Validate syntax of the modified source before persisting. Bail if SyntaxError.
1. **Backup before write**: Copy current file to `/opt/clawdia/<filename>.bak-YYYYMMDD-HHMMSS-<reason>` before overwriting.
1. **Restart + verify**: After patching the bot, `systemctl restart clawdia` then check `journalctl --since '15 seconds ago'` for `tools: N` startup line and any `Traceback`.

## Behavioral rules (in memory + system prompt)

- **DRIVE-SAVE RULE** (memory #9): Default destination for any file Clawdia creates is [durginfamily@gmail.com](mailto:durginfamily@gmail.com) (family Drive). Only save to [seandurgin@gmail.com](mailto:seandurgin@gmail.com) when Sean explicitly says so.
- **SECRET HANDLING RULE** (memory #7): All API key provisioning via `read -s -p` silent paste, never echoed.
- **SHIP-AND-DEMO RULE** (memory #8): When shipping a new tool, end with one concrete copy-paste-able test command for Sean's Telegram.
- **SCHEDULE RULE** (memory #11): Sean works long shifts and has plenty of time during them. Do NOT suggest wrapping early or deferring builds based on assumed time pressure.
- **Oracle air-gap** (system prompt): Clawdia never reads, searches, or summarizes the `1.Oracle` Gmail label. Sean's TS/SCI posture requires strict separation. HR artifacts about Sean's employment (paystubs, offer letter) ARE fine for things like resume work — those aren't classified work product. *Refinement pending in backlog.*

## Anti-fabrication

Multiple layers of guard against Clawdia inventing tool-error strings or capability claims:
- ABSOLUTE RULE in system prompt against fabricating tool-error messages without invoking the tool
- Per-turn audit logging in `ask_claude` (`AUDIT[chat=X] tools=[...] prior_used_tools=bool`) catches false-positive claims
- Capabilities & Honesty section forbids saying-she-did-X-without-calling-a-tool

# 12. Operational Procedures


## Re-auth Google OAuth

App `ClawDawgAccess` was published to production 2026-04-23, so tokens no longer expire after 7 days. If a token is somehow lost:
1. On Sean's Mac: `python3 ~/reauth_google.py` (uses `InstalledAppFlow`)
1. Consent in browser
1. `scp ~/google_token.json root@209.38.49.104:/etc/clawdia/`
1. `chmod 600 /etc/clawdia/google_token.json`
1. `systemctl restart clawdia`
Family token: same flow with `reauth_google_family.py`.

## Re-auth Microsoft OAuth

1. On Mac: `python3 ~/reauth_ms.py` (MSAL device-code flow)
1. Visit URL, enter device code, consent
1. `scp ~/ms_token.json root@209.38.49.104:/etc/clawdia/`
1. `chmod 600 /etc/clawdia/ms_token.json`
1. `systemctl restart clawdia`
Scopes currently granted: `Notes.ReadWrite`, `Mail.Read`, `Mail.ReadWrite`, `Mail.Send`, `Calendars.Read`, `User.Read`.

## Re-link Plaid (when a bank token expires)

1. On VPS: regenerate `plaid_link.html` (Sean's Plaid Client ID is baked into `plaid_link.py`)
1. `scp` to Mac, open in browser
1. Click through Plaid Link UI for the affected institution
1. Browser shows a copy-paste exchange command that already includes `set -a && source /etc/clawdia/env && set +a` prefix
1. Paste into VPS shell, run, token file updates
1. No service restart needed (token is read on demand)

## Backup

```javascript
cd /opt/clawdia && bash backup.sh
```
Manual; not automated yet. Backups land alongside source files as `*.bak-<timestamp>-<reason>`. 105 backups currently exist; cleanup is manual if disk gets tight.

## Restore from backup

```javascript
cp /opt/clawdia/bot_new.py.bak-YYYYMMDD-HHMMSS-<reason> /opt/clawdia/bot_new.py
systemctl restart clawdia
journalctl -u clawdia --since '15 seconds ago' | grep -E 'tools:|ERROR|Traceback'
```

## Mac listener restart

```javascript
launchctl unload ~/Library/LaunchAgents/com.clawdia.imessage.plist
launchctl load ~/Library/LaunchAgents/com.clawdia.imessage.plist
curl -sS http://100.77.185.52:8733/health
```
Also requires Full Disk Access granted to `/Library/Developer/CommandLineTools/.../python3.9` in System Settings → Privacy → Full Disk Access.

## Token rotation (Telegram bot)

Via `@BotFather` in Telegram. Old token auto-revokes on rotation. The httpx logger muzzle in `bot_new.py` prevents the new token from being written to journalctl.

# 13. Common Failure Modes

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

# 14. Things NOT Connected (worth knowing)

Clawdia does NOT have:
- Permanent file deletion (only trash with 30-day recovery)
- Drive sharing/permission changes (in PROHIBITED list)
- Banking transactions (read-only via Plaid)
- Account creation on any service
- HomeKit / Alexa / smart-home control (declined 2026-05-05)
- WGU portal access (backlog, requires Playwright)
- Spotify/Apple Music for Artists (backlog)
- FamilySearch genealogy (backlog)
- Drive `drive_create_doc` for arbitrary Word docs (backlog; create_google_sheet/create_spreadsheet/create_google_doc cover most needs)
- Drive `drive_upload_file` for arbitrary uploads (backlog)

# 15. Where to Find Things

- **What got built and why** → Notion Enhancement Backlog page (chronological, 10K+ words of decisions)
- **What exists right now** → this page (current architectural state)
- **What to build next** → backlog open `[ ]` items, Tier 1-3 by payoff
- **Memory rules** → `save_memory` / `delete_memory` tools or query the SQLite `memory` table directly
- **Conversation history** → `history` table (rolling)
- **Past Claude conversations about Clawdia** → use `conversation_search` from any [Claude.ai](http://claude.ai/) session
---
*Last updated: 2026-05-06 night. Update this page when major architectural changes ship — new services, new module categories, new auth flows, or significant tool reorganization. The Enhancement Backlog stays the place for individual item history; this stays the place for current state.*
*Update history:*
- *2026-05-06 night: tool count 116 → 124 (added Gmail draft mode + Gmail attachments on outbound + Drive .docx in-place edit — 8 new tools across Phases 1, 2, 3 of the resume use case build).*
- *2026-05-06 evening: tool count 98 → 116 (added Gmail attachment reading + Gmail organize/maintain suite + Outlook search/folder); confirmed **`plaid_recurring.py`** as separate module file (it was, audit was wrong to suspect otherwise); doc audit reconciled with reality.*