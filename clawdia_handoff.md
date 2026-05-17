# Clawdia Bootloader

This is the minimum every fresh Claude session needs to start working on Clawdia.
It does NOT track tools, features, or open work — that lives in **`/opt/clawdia/docs/backlog.md`** on the VPS (migrated off Notion 2026-05-16). Always check the backlog before starting work; it has a long tail of "won't do" items that will save you from rebuilding things Sean already declined.

---

## What Clawdia is

A personal AI assistant Telegram bot for Sean Durgin. Runs as `clawdia.service` on a DigitalOcean VPS. Single-user — locked to Sean's `OWNER_TELEGRAM_ID`. Tool-using Anthropic Sonnet 4.6 agent with ~153 tools spanning Gmail (personal + family), Drive, Calendar, Notion, Plaid (production, 4 banks), iMessage + Apple Notes + Apple Reminders + Apple Photos via Mac bridge, UniFi network, YouTube analytics, Apify (Facebook Marketplace + Google Flights), TeamSnap iCal feeds, briefing system with health heartbeat, debt tracking, multi-step workflows, scheduled tasks, and `clawdia_ssh` (self-administer the VPS).

## Server

- **VPS:** DigitalOcean Ubuntu 24.04 NYC3, IP `209.38.49.104` (Tailnet `100.122.55.112`). **Egress IP is `159.203.167.41`** — use this when allowlisting Clawdia in any external API.
- **SSH:** `ssh -T root@209.38.49.104` — key-based, no password. `-T` suppresses the "no pseudo-terminal" warning when running through Desktop Commander.
- **Bot file:** `/opt/clawdia/bot_new.py` (~10,500 lines, ~560KB)
- **Sibling modules:** `apify_marketplace.py` (Facebook Marketplace + Google Flights airfare), `briefing.py` + `briefing_sources.py` (morning briefing), `debt_tracking.py` (debt accounts/balance history).
- **Venv:** `/opt/clawdia/venv/bin/python3` — use this for any in-process testing.
- **Service:** `systemctl {restart,status,stop,start} clawdia`
- **Systemd unit:** `/etc/systemd/system/clawdia.service` has `TimeoutStopSec=10` + `KillMode=mixed` (caps shutdown hangs at 10s; added 2026-05-16).
- **Logs:** `journalctl -u clawdia -n 50 --no-pager` (use `--since '5 min ago'` for recent; never `-f` from a non-interactive shell).
- **Backup before risky edits:** `cd /opt/clawdia && bash backup.sh` — commits to local git, pushes to GitHub master. backup.sh has a Sysmon ERR-trap; failed backups now alert via the alert bot.
- **GitHub:** `seandurgin/clawdia` master branch (pushed via SSH deploy key at `/root/.ssh-clawdia-deploy/id_ed25519`, alias `github-clawdia` in `/root/.ssh/config`).
- **Sidecar service:** `clawdia-dashboard.service` (FastAPI LCARS dashboard on Tailnet at `100.122.55.112:8090`, family-facing). Also publicly reachable at `https://dashboard.seandurgin.com` (Netlify + Cloudflare), but `/api/*` reverse-proxy is unwired — public version shows AWAITING DATA FEED on all panels.
- **Mac bridge:** `~/clawdia_imessage_listener.py` on Sean's Mac, served at Tailnet `100.77.185.52:8733`. Bridges iMessage, Apple Notes, Apple Reminders, and Apple Photos (via SQLite reads). Has `_wait_for_tailscale_interface()` guard before bind so it doesn't crash-loop at boot before Tailscale comes up.

## Auth files (all `600 root:root` on VPS)

| Path | Owner |
|---|---|
| `/etc/clawdia/google_token.json` | `seandurgin@gmail.com` (personal Gmail + personal Drive + personal Calendar) |
| `/etc/clawdia/google_token_family.json` | `durginfamily@gmail.com` (family Gmail + family Drive) |
| `/etc/clawdia/ms_token.json` | Microsoft Graph (deprecated; Sean migrated off Outlook/OneNote) |
| `/etc/clawdia/plaid_tokens.json` | Plaid production (USAA, APG FCU, Chase, Citibank) |
| `/etc/clawdia/env` | All env vars (`EnvironmentFile=` in systemd unit). Includes `TELEGRAM_TOKEN`, `ANTHROPIC_API_KEY`, `NOTION_TOKEN`, `APIFY_API_TOKEN`, `CLOUDFLARE_API_TOKEN`, `ALERT_BOT_TOKEN`, `ALERT_CHAT_ID`, `CLAWDIA_IMESSAGE_URL`, `CLAWDIA_IMESSAGE_TOKEN`, etc. |
| `/opt/clawdia/.env` | iCloud creds (`ICLOUD_EMAIL`, `ICLOUD_APP_PASSWORD`) |

Google + MS tokens refresh on startup and every hour via background scheduler. Google OAuth app `ClawDawgAccess` is in production — tokens don't expire after 7 days.

## Workflow

You have **direct shell access to Sean's Mac** via the `Desktop Commander` MCP, and the Mac has key-based SSH to the VPS. So:

```
Claude → DC:start_process("ssh -T root@209.38.49.104 'cmd'") → VPS
```

No copy-paste bridge needed. Sean is no longer the human shell tunnel — he's the decision-maker and tester. You run commands directly.

**Use Desktop Commander as your default shell tool, NOT Macos:Shell.** Macos:Shell is unstable — dies mid-session and only recovers via a Claude app restart. DC has the same SSH capability plus richer features (`read_file`, `write_file`, `edit_block`, `list_processes`, etc.). Reserve Macos:Shell for osascript / AppleScript only. (Memory rule #16, established 2026-05-16 after 3 Macos:Shell failures in one session.)

The VPS network is allowlisted in your sandbox, so you cannot SSH from sandbox directly; always go through `DC:start_process`.

### How to edit `bot_new.py` (canonical pattern, post-2026-05-16)

The old "ssh + python3 heredoc" pattern works for tiny edits but breaks on anything involving apostrophes, emojis, or large code blocks. Three failure modes hit repeatedly:

1. **Shell-escape collision** — `'"'"'` (close-quote / escaped-quote / open-quote) inside a `<< 'PYEOF'` heredoc doesn't get interpreted, leaves literal `\''` chars in your Python source.
2. **Unicode surrogate pairs** — `\ud83c\udf96` (🎖️) renders as paired surrogates that `utf-8` codec rejects when writing to disk.
3. **Slice-offset drift** — hardcoded `lines[NNN:MMM]` slices break the moment the file grows. Use regex extraction instead.

The pattern that always works:

```bash
# 1. Write the patch script locally (in chat-side container)
cat > /tmp/patch_NAME.py << 'PYEOF'
import sys, ast, shutil
BOT = "/opt/clawdia/bot_new.py"
shutil.copy(BOT, "/tmp/bot_new.py.pre-NAME")
with open(BOT) as f:
    src = f.read()
# ... edits ...
ast.parse(src2)
with open(BOT, "w") as f:
    f.write(src2)
print(f"OK delta={len(src2)-len(src):+d}")
PYEOF

# 2. scp to VPS
scp /tmp/patch_NAME.py root@209.38.49.104:/tmp/patch_NAME.py

# 3. Run remotely
ssh -T root@209.38.49.104 "python3 /tmp/patch_NAME.py"
```

For small surgical edits, `DC:edit_block` works directly on remote files via the same SSH bridge — try it for anything under ~20 lines.

Always anchor on a substring grep-verified to appear exactly once. Always `ast.parse` before writing back. Always `systemctl restart clawdia && sleep 4 && journalctl --since '15 sec ago'` to confirm clean boot + tool count.

### Docs tools (use these for backlog/architecture/conventions queries)

As of 2026-05-16, Claude-facing docs migrated off Notion to `/opt/clawdia/docs/*.md`. Five new tools handle them:

- `docs_list()` — list files
- `docs_read(file)` — full content
- `docs_search(query, max_results=50)` — substring search across all docs
- `docs_edit(file, old_str, new_str)` — surgical str_replace, unique-anchor required
- `docs_append(file, content)` — append to end of file

These are sub-second; Notion API timeouts on the same content used to take 5-30s and fail intermittently. **When you need to update the backlog after shipping, use `docs_edit` against `backlog.md` — don't touch Notion.**

## Standing rules (non-negotiable, in priority order)

1. **SECRET HANDLING** (memory #7) — never put credential values in chat, command-line args, or `bash -x` output. Use `read -s -p "Paste key: " VAR` for interactive secrets; never `echo` or `cat` an env value. Even token prefixes (first 12 chars) are leak surface — don't display them as "verification."
2. **SAFE-TRACE** (memory #13) — never `bash -x` a script that sources `/etc/clawdia/env` (or any env file with secrets). Use `/opt/clawdia/scripts/safe_trace.sh <script>` instead, which redacts TOKEN/KEY/SECRET/PASSWORD/CLIENT_ID/CLIENT_SECRET vars before printing. Established after a real exposure incident 2026-05-07.
3. **EGRESS-IP** (memory #14) — VPS inbound SSH is `209.38.49.104`, but egress (outbound HTTP) is `159.203.167.41`. When allowlisting Clawdia in an external API, use the egress IP. `curl -s ifconfig.me` from the VPS confirms.
4. **DESTRUCTIVE-UI WARNINGS** (memory #12) — when instructing on cleanup in cloud portals (Azure, GCP, AWS), name destructive buttons that look like safe ones. "Delete secret" vs "Delete app registration" — specify which to click and which not to. Established after accidental Azure app deletion.
5. **CLOUD-NATIVE ROTATION** (memory #11) — when rotating cloud credentials, check if the provider offers a native "Rotate" before prescribing create-new-and-delete-old. Native rotation preserves metadata + gives an overlap window.
6. **SHIP-AND-DEMO** (memory #2) — every shipped feature ends with one copy-paste-ready Telegram test command, not abstract phrasing. ❌ "ask her to watch for milwaukee batteries" ✅ "Paste this into Telegram: Search Marketplace for milwaukee m18 batteries under $100"
7. **DRIVE-SAVE DEFAULT** (memory #9) — files Clawdia creates land in `durginfamily@gmail.com` (family Drive). Personal Drive only when Sean explicitly says so.
8. **SCHEDULE** (memory #10) — Sean works long shifts and has time during them. Don't suggest deferring or wrapping early.
9. **END-OF-SESSION** (memory #15) — don't suggest wrapping up after a ship, even framed as praise. Sean decides when the session ends. Just report state and stop.
10. **SHELL-TOOL DEFAULT** (memory #16) — Desktop Commander, NOT Macos:Shell. Use `ssh -T root@209.38.49.104 "cmd"` form.
11. **CHECK THE BACKLOG FIRST** — before building anything, `docs_search` `/opt/clawdia/docs/backlog.md` for the feature name. Dozens of "won't do" items with full reasoning. Don't repeat closed work.
12. **BACKUP BEFORE BIG EDITS** — `cd /opt/clawdia && bash backup.sh` before editing `bot_new.py` for any non-trivial change.
13. **SYNTAX CHECK BEFORE RESTART** — `python3 -c "import ast; ast.parse(open('/opt/clawdia/bot_new.py').read())"` before `systemctl restart clawdia`.
14. **VERIFY AFTER RESTART** — `systemctl is-active clawdia` and check `journalctl --since '15 sec ago' | grep tools:` for clean boot and tool count.

## Family

- **Sean Durgin** — owner, retired USAF MSgt (E-7), currently Oracle Data Center Technician (TS/SCI CI Poly), 113 Cool Springs Rd, North East, MD 21901
- **Wife:** Heather. **Kids:** Aaron, Hailey, Jonah, Evan.
- **Oracle air-gap:** the `1.Oracle` Gmail label is OFF-LIMITS. Clawdia must never read, search, or summarize it. Hard rule in system prompt.

## Fabrication discipline

Clawdia has been observed in three fabrication shapes; the system prompt explicitly addresses all three:

- **Shape A (past-action):** claims something was done that wasn't (or denies doing something the audit log shows was done). Cure: check the audit log / DB before asserting.
- **Shape B (search-empty-inference):** memory_search returns nothing → instead of saying "I don't know," Clawdia confabulates plausible-sounding answers. Cure: when search empty, say so and ask for the input.
- **Shape C (completed-setup-work):** claims setup work is done ("table created," "cache written," "migration applied") before actually running the commands. Cure: verify via shape-C check (`SELECT COUNT(*)` / `grep -c` / `ls`) before claiming completion.

The system prompt has a "Verification Before Completion-Claim" section naming all three shapes. The `_ACTION_CLAIM_PATTERNS` regex array catches shape-C verbs in Clawdia's own draft responses and forces a verification tool call before the message ships.

## Where to go from here

- **What's open / what's been declined:** `docs_read('backlog.md')` or `docs_search` for a topic
- **System architecture:** `docs_read('architecture.md')` — has a "last refreshed" stamp at top; if facts seem stale, refresh it AND log under Top recent ships in backlog
- **Working conventions:** `docs_read('conventions.md')`
- **Past session handoffs:** `/opt/clawdia/docs/archive/` has ~9 historical session-handoff files
- **What tools exist right now:** `grep '"name":"' /opt/clawdia/bot_new.py | wc -l` or ask Clawdia in Telegram: "list your tools"
