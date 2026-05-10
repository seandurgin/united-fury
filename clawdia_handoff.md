# Clawdia Bootloader

This is the minimum every fresh Claude session needs to start working on Clawdia.
It does NOT track tools, features, or open work — that lives in the **Enhancement Backlog** (Notion page `3442e075-ac64-8186-aa93-efdcb4ff5934`), which is the single source of truth for what's done, declined, and pending. Always check the backlog before starting work; it has a long tail of "won't do" items that will save you from rebuilding things Sean already declined.

---

## What Clawdia is

A personal AI assistant Telegram bot for Sean Durgin. Runs as `clawdia.service` on a DigitalOcean VPS. Single-user — locked to Sean's `OWNER_TELEGRAM_ID`. Tool-using Anthropic Sonnet agent with ~116 tools spanning Gmail (personal + family), Drive (read/write/upload/organize, both Drives), Calendar (Google + iCloud read/write/move), Notion, Plaid (production, 4 banks), iMessage (via Mac bridge), Apple Notes, Apple Reminders, UniFi network, YouTube analytics, Apify Marketplace, Google Maps Distance Matrix (commute ETA), image gen, voice transcription, memory (save/search across 456+ entries), scheduled tasks, multi-step workflows, and `clawdia_ssh` (self-administer the VPS).

## Server

- **VPS:** DigitalOcean Ubuntu 24.04 NYC3, IP `209.38.49.104` (Tailnet `100.122.55.112`)
- **VPS egress IP:** `159.203.167.41` — different from inbound; use this for API key restrictions (memory rule #14)
- **SSH:** `ssh root@209.38.49.104` — key-based, no password (alias `clawdia` works from Sean's Mac)
- **Bot file:** `/opt/clawdia/bot_new.py` (~10K+ lines, 424KB)
- **Venv:** `/opt/clawdia/venv/bin/python3` (use this for any in-process testing)
- **Service:** `systemctl {restart,status,stop,start} clawdia`
- **Logs:** `journalctl -u clawdia -n 50 --no-pager` (use `--since '5 min ago'` for recent; never `-f` from a non-interactive shell)
- **Backup before risky edits:** `cd /opt/clawdia && bash backup.sh` (commits to local git AND pushes to GitHub)
- **GitHub:** `seandurgin/clawdia` master branch (only branch on remote; pushed via SSH deploy key at `/root/.ssh-clawdia-deploy/id_ed25519`, alias `github-clawdia` in `/root/.ssh/config` — no PAT in remote URL)
- **Sidecar service:** `clawdia-dashboard.service` (FastAPI dashboard on Tailnet at `100.122.55.112:8090`, family-facing)
- **Sysmon bot:** separate Telegram bot (`@clawdia_sysmon_bot`, bot_id 8410207311) for ops alerts; channel-separated from main Clawdia bot so health-check alerts don't pollute Sean's chat

## Auth files (all `600 root:root`)

| Path | Owner |
|---|---|
| `/etc/clawdia/google_token.json` | `seandurgin@gmail.com` |
| `/etc/clawdia/google_token_family.json` | `durginfamily@gmail.com` |
| `/etc/clawdia/plaid_tokens.json` | Plaid production (USAA, APG FCU, Chase, Citibank) |
| `/etc/clawdia/teamsnap_token.json` | TeamSnap (when shipped — currently paused mid-OAuth) |
| `/etc/clawdia/env` | All env vars (loaded as `EnvironmentFile` by systemd unit) — single source of truth |

Google tokens refresh on startup and every hour via background scheduler. Google OAuth app `ClawDawgAccess` is in production — tokens don't expire after 7 days.

**Microsoft Graph integration was deprecated 2026-05-07** after Azure app was accidentally deleted during cleanup. OneNote and Outlook tools removed (14 tools), `ms_token.json` no longer used, `AZURE_*` env vars removed.

**iCloud creds** live in `/etc/clawdia/env` (`ICLOUD_EMAIL=seanldurgin@icloud.com`, `ICLOUD_APP_PASSWORD`). Previously in `/opt/clawdia/.env`; consolidated 2026-05-09.

## Workflow

You have **direct shell access to Sean's Mac** via the `Macos:Shell` tool, and the Mac has key-based SSH to the VPS. So:

```
Claude → Macos:Shell("ssh clawdia '...'") → VPS
```

No copy-paste bridge needed. Sean is no longer the human shell tunnel — he's the decision-maker and tester. You run commands directly.

The VPS network is allowlisted in your sandbox, so you cannot SSH from sandbox; you must always go through `Macos:Shell`.

## Standing rules (non-negotiable, in priority order)

1. **SECRET HANDLING (memory #11)** — never put credential values in chat, command-line args, or any logged location. Use `read -s "VAR?prompt: "` (zsh) and SSH the value via stdin redirect. **Provision one credential per command — never use multi-prompt single-line commands** (lesson from 2026-05-09 TeamSnap session).
2. **SAFE-TRACE (memory #11)** — never `bash -x` directly on a Clawdia script that sources `/etc/clawdia/env`. Use `/opt/clawdia/scripts/safe_trace.sh <script>` which redacts secret-bearing variables.
3. **DESTRUCTIVE-UI (memory #12)** — when instructing on cloud-portal cleanup, name the destructive button AND the adjacent safe button. Trash/Destroy buttons sit next to Edit/Save buttons by design.
4. **CLOUD-NATIVE ROTATION (memory #13)** — check provider's native Rotate/Refresh button before manual create-new-and-delete-old. Native rotation usually preserves metadata and gives a graceful overlap window.
5. **EGRESS-IP (memory #14)** — when restricting an API key to "this server's IP", use the OUTBOUND/egress IP (`159.203.167.41`), not the inbound SSH IP. Mismatch returns REQUEST_DENIED with the actual egress IP in the error message — that's the diagnostic.
6. **SHIP-AND-DEMO (memory #8)** — every shipped feature ends with one copy-paste-ready Telegram test command, not abstract phrasing.
7. **DRIVE-SAVE DEFAULT (memory #9)** — files Clawdia creates land in `durginfamily@gmail.com` (family Drive). Personal Drive only when Sean explicitly says so.
8. **SCHEDULE (memory #10)** — Sean works long shifts and has time during them. Don't suggest deferring or wrapping early. He'll say when he's done.
9. **CHECK THE BACKLOG FIRST** — before building anything, fetch the Notion enhancement backlog and grep for the feature name. There are dozens of "won't do" items with full reasoning, and several "already shipped" surprises in the recent record. Don't repeat closed work.
10. **BACKUP BEFORE BIG EDITS** — `cd /opt/clawdia && bash backup.sh` before editing `bot_new.py` for any non-trivial change.
11. **PATCHER DISCIPLINE** — always: backup → patcher with anchors → compile validation (`compile(src, PATH, "exec")`) → critical-names check → atomic write (only on full success) → restart → smoke test + live functional test → commit → push → Notion update. Atomicity at the patcher level is what enables the zero-rollback streak.
12. **VERIFY AFTER RESTART** — `systemctl is-active clawdia` and `journalctl -u clawdia --since "30 sec ago" --no-pager | grep -E "Starting Clawdia|health check|ERROR"` to confirm clean boot and tool count.

## Family

- **Sean Durgin** — owner, retired USAF MSgt (E-7, 21+ years, 1D771Q Cyber Defense), currently Oracle Data Center Technician (TS/SCI CI Poly)
- **113 Cool Springs Rd, North East, MD 21901**
- **Wife:** Heather. **Kids:** Evan, Jonah, Hailey, Aaron.
- **Oracle air-gap:** the `1.Oracle` Gmail label is OFF-LIMITS. Clawdia must never read, search, or summarize it. Hard rule in system prompt.

## Where to go from here

- **What's open / what's been declined:** [Enhancement Backlog](https://www.notion.so/3442e075ac648186aa93efdcb4ff5934) (page id `3442e075-ac64-8186-aa93-efdcb4ff5934`)
- **What tools exist right now:** grep `bot_new.py` for the `TOOLS = [` block, or ask Clawdia in Telegram: "list your tools"
- **Architectural state of Clawdia** (services, env vars, file layout): [Architecture & Operations Reference](https://www.notion.so/3572e075ac6481958550cb3b18f1840b)
- **How Claude builds Clawdia (durable conventions):** [Claude's Working Conventions for Clawdia Sessions](https://www.notion.so/3522e075ac6481478629c264aafc90e6)
- **Older session-by-session handoffs:** [📦 Clawdia Archive](https://www.notion.so/3592e075ac6481958b53f29a8629fbd7)
