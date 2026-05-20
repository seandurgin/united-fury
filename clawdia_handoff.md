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
11. **DC-BACKGROUND** (memory #17, added 2026-05-18) — never use `&` to background a process inside `DC:start_process`. Backgrounded processes hold the shell and lock up DC, requiring a Claude Desktop restart. Use `timeout N cmd` instead of `cmd & sleep N; kill $!`. Safest: synchronous calls, rely on DC's own timeout. Established after `dns-sd -B &` hung DC and broke a session.
12. **CHECK THE BACKLOG FIRST** — before building anything, `docs_search` `/opt/clawdia/docs/backlog.md` for the feature name. Dozens of "won't do" items with full reasoning. Don't repeat closed work.
13. **BACKUP BEFORE BIG EDITS** — `cd /opt/clawdia && bash backup.sh` before editing `bot_new.py` for any non-trivial change.
14. **SYNTAX CHECK BEFORE RESTART** — `python3 -c "import ast; ast.parse(open('/opt/clawdia/bot_new.py').read())"` before `systemctl restart clawdia`.
15. **VERIFY AFTER RESTART** — `systemctl is-active clawdia` and check `journalctl --since '15 sec ago' | grep tools:` for clean boot and tool count.

## Family

- **Sean Durgin** — owner, retired USAF MSgt (E-7), currently Oracle Data Center Technician (TS/SCI CI Poly), 113 Cool Springs Rd, North East, MD 21901
- **Wife:** Heather. **Kids:** Aaron, Hailey, Jonah, Evan.
- **Oracle air-gap:** the `1.Oracle` Gmail label is OFF-LIMITS. Clawdia must never read, search, or summarize it. Hard rule in system prompt.

## Fabrication discipline

Clawdia has been observed in three fabrication shapes; the system prompt explicitly addresses all three:

- **Shape A (past-action):** claims something was done that wasn't (or denies doing something the audit log shows was done). Cure: check the audit log / DB before asserting.
- **Shape B (search-empty-inference):** memory_search returns nothing → instead of saying "I don't know," Clawdia confabulates plausible-sounding answers. Cure: when search empty, say so and ask for the input.
- **Shape C (completed-setup-work):** claims setup work is done ("table created," "cache written," "migration applied") before actually running the commands. Cure: verify via shape-C check (`SELECT COUNT(*)` / `grep -c` / `ls`) before claiming completion.
- **Shape D (claimed-category-placement, identified 2026-05-18):** after `save_memory(cat, key, value)` returns, Clawdia narrates the category she REQUESTED rather than the category the data actually LANDED in. The `memory_save` cross-cat guard (v3b, see "Memory drift protection" below) silently redirects writes to preserve canonical-fact-lives-in-one-place; Clawdia doesn't see the redirect and reports the requested category as fact. Cure pending — Path A (memory_save returns actual stored category tuple to Clawdia) is correct fix, Path B (system-prompt rule suppressing category mentions in responses) is cheap interim. See backlog.
- **Shape E (same-category-key-proliferation, identified 2026-05-18):** Clawdia saves the same fact multiple times under DIFFERENT KEYS in the SAME category, because she doesn't check for existing memory before writing. The v3b guard is cross-category only; same-category Pass 1 dedup requires byte-identical values. First observed: 3 new preferences rows about Star Trek/TNG saved within 32 minutes, all paraphrases of an existing `preferences/star_trek_fan` row from May 12. Cure pending — algorithmic fix (Path B in backlog) extends Pass 1 to use identifier-substring + length-gated containment, same logic as v3b's Pass 2 but scoped to same-category matches. See backlog.

The system prompt has a "Verification Before Completion-Claim" section naming Shapes A-C; Shapes D and E are newer and not yet in-prompt. The `_ACTION_CLAIM_PATTERNS` regex array catches shape-C verbs in Clawdia's own draft responses and forces a verification tool call before the message ships.

## Memory drift protection (v3b, shipped 2026-05-18)

`memory_save(cat, key, value)` has a 2-pass dedup guard. Pass 1 catches same-category-same-value re-saves. Pass 2 catches cross-category drift — if `key` exists in another category and either (a) one value is a substring of the other (length-gated to ≥25 normalized chars) OR (b) they share a digit-containing alphanumeric identifier token ≥6 chars (e.g. member numbers, account IDs, EINs), the write is redirected to update the existing row in its original category. **Side effect**: Clawdia doesn't know about the redirect — this causes Shape D fabrication (see above). When debugging "save_memory said it saved to category X but the row is in category Y", that's working-as-designed, not a bug. Memory total rows: 443 (was 478 before this ship; 28 cross-cat duplicate keys consolidated).

## Alienware bridge (shipped 2026-05-18)

Clawdia can run read-only commands on Sean's Alienware Ubuntu desktop via `alienware_exec` tool. Bridge daemon runs as `sean` (uid 1000, NOT root) on Alienware, binds to Tailnet IP `100.70.41.23:8734` (NOT 0.0.0.0). FastAPI + uvicorn in venv at `~/.clawdia_bridge/venv/`. Code at `~/.clawdia_bridge/bridge.py` (MD5 `1a110dcfe6b7f4be516d30487a357ca4`). Service `clawdia-bridge.service` (user systemd, lingered). Auth via bearer token at `/etc/clawdia_bridge/token` on Alienware (mode 600, MD5 prefix `699eb0a8ba151d1c`), matched by `CLAWDIA_ALIENWARE_BRIDGE_TOKEN` in `/etc/clawdia/env` on VPS. Allowlist (Tier 1, read-only): ls, cat, find, grep, head, tail, wc, du, df, ps, free, uptime, whoami, hostname, pwd, which, file, stat, journalctl (no --vacuum/--rotate/--flush), tree, id, date, uname, echo, printenv, ip, ss, systemctl (status/is-active/list-units only). Rejects shell metacharacters (`|`, `>`, `<`, `&`, `;`, backtick, `$()`, `&&`, `||`, `>>`, `<<`, `..`). 30s timeout, 50KB stdout cap, workdir `/home/sean`. Audit log JSON-lines at `~/.clawdia_bridge/audit.log` (byte counts only, never stdout content). UFW rule on Alienware: `allow from 100.64.0.0/10 to any port 8734 proto tcp` (Tailscale CGNAT range only). Tool count went 153 → 154 with this ship.

**Known residuals (logged in backlog):** (1) `StartLimitIntervalSec` is in `[Service]` of the unit file but belongs in `[Unit]` — silently ignored, rate-limit-on-failure not applied. (2) 401 auth failures don't reach the audit log (rejected before audit code path). (3) An unexplained Clawdia restart at 19:26:00 UTC during ship, before our 19:15:40 deployment took full effect — not investigated. (4) `bash backup.sh` failed to push for one of today's commits earlier; eventually resolved.

**Mac side is NOT shipped.** Same daemon for macOS via launchd is deferred (see backlog entry). When built, will likely become `host_exec(target, cmd)` with multi-target dispatch rather than a second `mac_exec` tool.

## Data Registry (single source of truth, established 2026-05-20)

**Cardinal rule:** every kind of data has ONE authoritative home. Everything else is a
read-only derived view. Data flows one direction: source -> view, never bidirectional.

**Before creating ANY new tracker, sheet, note, or database:** consult this registry.
If a home exists for that entity, write there. Do NOT spin up a parallel store. If no
home exists, propose adding a registry row to Sean FIRST.

Authoritative homes:
- **Personal todos** -> Notion "Sean's To-Do" DB id=2692e075-ac64-8040-b028-d974d8f1e651 (exists). NOT Apple Reminders/OneNote as masters.
- **Health/medical** -> Notion Health DB (BUILD). Appt times also on calendar as a view.
- **Bills/utilities** -> Notion "Budget" DB id=dbc2e075-ac64-830c-957f-81514623b5d5 (EXISTS - do not create a new Finance DB). Google Sheet is analysis layer only.
- **Financial accounts** -> Plaid (live, already clean). Never duplicate balances.
- **Vehicles** -> Notion Vehicles DB (BUILD).
- **Projects/learning** (PenTest+, CISM, School) -> Notion project pages (one per project).
- **Kids activities/sports** -> TeamSnap + league ical_feeds (read via ical_feed tools).
- **Reference docs** -> Google Drive family (DRIVE-SAVE rule).
- **Clawdia operational config** (scheduled_tasks, workflows, monitors, memory) -> SQLite. Correct as-is.

Calendar (split by origin, Clawdia reads all, 365d lookahead):
- iCloud Family = shared household + DEFAULT for ambiguous writes
- iCloud Sean = personal; iCloud Oracle = personal Oracle schedule (paydays/on-call)
- Google durginfamily ("Joint") = family Google cal (Sean fixing Mac auth)
- webcal feeds = TeamSnap/school/league (read-only)
- Clawdia never creates a parallel "Clawdia calendar"; writes go to the owning calendar.

Net-new homes still to build: Notion Health DB, Vehicles DB, project pages. (Finance uses existing Budget DB; Todos uses existing Sean's To-Do DB.)

**Disney trip-planning DBs (cleaned up 2026-05-20):** Notion has 4 Disney park DBs - "Disney - EPCOT" (b022e075), "Disney - Hollywood Studios" (e632e075), "Disney - Magic Kingdom" (0502e075), "Disney - Animal Kingdom" (9c42e075). These were originally 8 blank-titled DBs: 4 unique park datasets each duplicated once (verified 100%/98% row-overlap). The 4 redundant twins were archived to Notion trash. Each park DB holds dining + attractions + (for AK) animal/exhibit rows. Do NOT merge the parks together (the per-park split is intentional); do NOT recreate the archived twins.
Until built, those entities have no home yet and the registry can't fully bind them.

Enforcement reality: this registry is in the bootloader read-path (you are reading it).
Memory-rule routing is unreliable (see Shape A). The real enforcement is a drift-detection
audit (not yet built). Prompt-level rules drift; structural enforcement holds.

## Where to go from here

- **What's open / what's been declined:** `docs_read('backlog.md')` or `docs_search` for a topic
- **System architecture:** `docs_read('architecture.md')` — has a "last refreshed" stamp at top; if facts seem stale, refresh it AND log under Top recent ships in backlog
- **Working conventions:** `docs_read('conventions.md')`
- **Past session handoffs:** `/opt/clawdia/docs/archive/` has ~9 historical session-handoff files
- **What tools exist right now:** `grep '"name":"' /opt/clawdia/bot_new.py | wc -l` or ask Clawdia in Telegram: "list your tools"
