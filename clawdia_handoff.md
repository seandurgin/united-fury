# Clawdia Bootloader

This is the minimum every fresh Claude session needs to start working on Clawdia.
It does NOT track tools, features, or open work — that lives in the **Enhancement Backlog** (Notion page `3442e075-ac64-8186-aa93-efdcb4ff5934`), which is the single source of truth for what's done, declined, and pending. Always check the backlog before starting work; it has a long tail of "won't do" items that will save you from rebuilding things Sean already declined.

---

## What Clawdia is

A personal AI assistant Telegram bot for Sean Durgin. Runs as `clawdia.service` on a DigitalOcean VPS. Single-user — locked to Sean's `OWNER_TELEGRAM_ID`. Tool-using Anthropic Sonnet agent with ~100 tools spanning Gmail, Drive, Calendar, OneNote, Notion, iCloud Calendar, Plaid (production, 4 banks), iMessage (via Mac bridge), Apple Notes, Apple Reminders, UniFi network, YouTube analytics, Apify Marketplace, image gen, voice transcription, scheduled tasks, multi-step workflows, and `clawdia_ssh` (self-administer the VPS).

## Server

- **VPS:** DigitalOcean Ubuntu 24.04 NYC3, IP `209.38.49.104` (Tailnet `100.122.55.112`)
- **SSH:** `ssh root@209.38.49.104` — key-based, no password
- **Bot file:** `/opt/clawdia/bot_new.py` (~5000 lines)
- **Venv:** `/opt/clawdia/venv/bin/python3` (use this for any in-process testing)
- **Service:** `systemctl {restart,status,stop,start} clawdia`
- **Logs:** `journalctl -u clawdia -n 50 --no-pager` (use `--since '5 min ago'` for recent; never `-f` from a non-interactive shell)
- **Backup before risky edits:** `cd /opt/clawdia && bash backup.sh` (commits to local git; remote push may fail and that's fine)
- **GitHub:** `seandurgin/openclaw-brain` clawdia branch (note: GitHub says repo moved to `seandurgin/clawdia` — remote URL fix is a stale TODO)
- **Sidecar service:** `clawdia-dashboard.service` (FastAPI dashboard on Tailnet at `100.122.55.112:8090`, family-facing)

## Auth files (all `600 root:root`)

| Path | Owner |
|---|---|
| `/etc/clawdia/google_token.json` | `seandurgin@gmail.com` |
| `/etc/clawdia/google_token_family.json` | `durginfamily@gmail.com` |
| `/etc/clawdia/ms_token.json` | Microsoft Graph (Outlook + OneNote, scopes include `Mail.Read`, `Mail.ReadWrite`, `Mail.Send`, `Notes.ReadWrite`, `Calendars.Read`, `Files.ReadWrite`) |
| `/etc/clawdia/plaid_tokens.json` | Plaid production (USAA, APG FCU, Chase, Citibank) |
| `/etc/clawdia/env` | All env vars (loaded as `EnvironmentFile` by systemd unit) |
| `/opt/clawdia/.env` | iCloud creds (`ICLOUD_EMAIL`, `ICLOUD_APP_PASSWORD`) |

Google + MS tokens refresh on startup and every hour via background scheduler. Google OAuth app `ClawDawgAccess` is in production — tokens don't expire after 7 days.

## Workflow

You have **direct shell access to Sean's Mac** via the `Macos:Shell` tool, and the Mac has key-based SSH to the VPS. So:

```
Claude → Macos:Shell("ssh root@209.38.49.104 '...'") → VPS
```

No copy-paste bridge needed. Sean is no longer the human shell tunnel — he's the decision-maker and tester. You run commands directly.

The VPS network is allowlisted in your sandbox, so you cannot SSH from sandbox; you must always go through `Macos:Shell`.

## Standing rules (non-negotiable)

1. **SECRET HANDLING** — never put credential values in chat or command-line args. Use `read -s -p "Paste key: " VAR` then redirect.
2. **SHIP-AND-DEMO** — every shipped feature ends with one copy-paste-ready Telegram test command, not abstract phrasing.
3. **DRIVE-SAVE DEFAULT** — files Clawdia creates land in `durginfamily@gmail.com` (family Drive). Personal Drive only when Sean explicitly says so.
4. **SCHEDULE** — Sean works long shifts and has time during them. Don't suggest deferring or wrapping early. He'll say when he's done.
5. **CHECK THE BACKLOG FIRST** — before building anything, fetch the Notion enhancement backlog and grep for the feature name. There are dozens of "won't do" items with full reasoning. Don't repeat closed work.
6. **BACKUP BEFORE BIG EDITS** — `cd /opt/clawdia && bash backup.sh` before editing `bot_new.py` for any non-trivial change.
7. **SYNTAX CHECK BEFORE RESTART** — `python3 -c "import ast; ast.parse(open('/opt/clawdia/bot_new.py').read())"` before `systemctl restart clawdia`.
8. **VERIFY AFTER RESTART** — `systemctl is-active clawdia` and `journalctl -u clawdia -n 30 --no-pager` to confirm clean boot and tool count.

## Family

- **Sean Durgin** — owner, retired USAF MSgt, currently Oracle Data Center Technician (TS/SCI CI Poly)
- **113 Cool Springs Rd, North East, MD 21901**
- **Wife:** Heather. **Kids:** Evan, Jonah, Hailey, Aaron.
- **Oracle air-gap:** the `1.Oracle` Gmail label is OFF-LIMITS. Clawdia must never read, search, or summarize it. Hard rule in system prompt.

## Where to go from here

- **What's open / what's been declined:** [Enhancement Backlog](https://www.notion.so/3442e075ac648186aa93efdcb4ff5934) (page id `3442e075-ac64-8186-aa93-efdcb4ff5934`)
- **What tools exist right now:** grep `bot_new.py` for the `TOOLS = [` block, or ask Clawdia in Telegram: "list your tools"
- **Recent session history:** Notion has session-handoff pages in the same workspace (parent of the backlog page)
