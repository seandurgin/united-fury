# Clawdia Session Handoff — May 16, 2026

<!-- Migrated from Notion 3622e075-ac64-8146-a13f-cc74de40c775 on 2026-05-16. Archived session handoff or historical doc. -->


## Current Status

- **Bot**: active, model `claude-sonnet-4-6`, **tools: 142**, last PID 730741
- **File**: `/opt/clawdia/bot_new.py` — 8780 lines
- **Last commit**: `d25b6c4` (Auto-backup 2026-05-16 00:59 UTC), pushed to GitHub `seandurgin/clawdia` master
- **Mac bridge**: 100.77.185.52:8733 — alive, Apple Photos endpoints working end-to-end (verified at 00:20 UTC with vision attachment)
- **Service unit**: `/etc/systemd/system/clawdia.service` now has `TimeoutStopSec=10` + `KillMode=mixed`. Live file is source of truth; copy at `/opt/clawdia/clawdia.service.systemd` for git history.
- **Logs**: `journalctl -u clawdia -n 50 --no-pager`
- **Backup**: `cd /opt/clawdia && bash backup.sh`

## What Shipped Today (3 substantive items)


### 1. Apple Photos search + read (commit 631a8aa, hotfix 036bb7c)

Two new tools (`photos_search`, `photo_read`) bridging VPS → Mac → Apple Photos SQLite over Tailscale. `photos_search` filters by date_from / date_to / person / ocr_contains (Apple on-device OCR via NSKeyedArchiver + LZFSE). `photo_read` returns a vision content block — Clawdia can actually see the image, verified end-to-end at 00:20 UTC when she described a Documentary Now TikTok screenshot in detail (Gil Ozeri, bold green caption text, 18.2K likes).
Mid-session bug caught + fixed: Mac bridge returns fields named `base64_data` + `mime_type`; VPS tool initially expected `image_base64` + `media_type`. Added fallbacks (check both names). Tool count 140 → 142.
Dispatcher kind-tuple extended: `imessage_attachment_payload` / `gmail_attachment_payload` / `photo_read_payload` (line ~5008 of bot_[new.py](http://new.py/)).
LaunchAgent `com.clawdia.imessage` had a latent Tailscale-up race at boot — zero successful boots ever logged in stdout, only worked after manual `launchctl kickstart`. Durability fix flagged but not built (see Open below).

### 2. systemctl restart hang — symptom fixed (commit eb01b0c)

Added `TimeoutStopSec=10` + `KillMode=mixed` to `/etc/systemd/system/clawdia.service`. Caps worst-case restart at 10s and force-kills lingering child processes.
Root cause confirmed via May 15 03:49 UTC log: `clawdia_ssh` spawned an SSH subprocess that kept the cgroup alive when SIGTERM arrived during an active tool call, blocking systemd cleanup until 30s+ TimeoutStop hit (logged as `Sent signal SIGKILL to main process ... on client request`). With `KillMode=mixed`, systemd now kills lingering children directly.
Verified with 3 rapid-fire restarts all <1s. Unit file copied to repo at `/opt/clawdia/clawdia.service.systemd` for git tracking.
**Underlying race NOT fixed** — bot_[new.py](http://new.py/) still has zero SIGTERM handlers (PTB 22.7's `run_polling()` installs defaults but in-flight `requests.*` without explicit timeouts can still block). Symptom-level fix gives ~95% of user-visible benefit at ~10% of risk.

### 3. Fabrication-rule shape-B and shape-C coverage (commit d25b6c4)

Extended `_ACTION_CLAIM_PATTERNS` with shape-C verbs (`table created`, `schema migrated`, `cache written`, `migration applied`, `database initialized`, `index built`, `config deployed`, `rows inserted into table`) all mapped to `clawdia_ssh`. Pattern count 14 → 16.
Added new system-prompt section **"Verification Before Completion-Claim"** between Capabilities & Honesty and Memory Discipline, naming shapes B and C explicitly:
- **Shape B (search-empty-inference)**: behavior rule — run ≥2 different search terms before claiming non-existence; if Sean's framing implies prior work, believe him.
- **Shape C (completed-setup-work)**: verification rule — if you claim a table/cache/schema was created, you must have called clawdia_ssh in this turn; otherwise READ BACK state before claiming completion.
- **Shape A** intentionally left alone — entry warned that false positive rate matters and existing coverage is OK.
Regex tests passed 9/9 valid cases including advice-context suppression. Shape B intentionally NOT given pattern coverage — too many false positives on denials.

## Honest Engineering Failures Today

- **Photos build burned ~half the session on transport.** First-attempt approach was base64 + chunked SSH appends, which got corrupted in transit (chat-rendering mutated chars like `headers=` → `headerss`). Pivoted to `ssh root@vps "python3 -" << 'PYEOF' ... PYEOF` heredoc pattern after the corruption was real, not cosmetic. The heredoc pattern was sitting in plain view in the LaunchAgent plist used by the bridge itself; should have reached for it on call #1. Documented as the canonical edit pattern in `clawdia_handoff.md` after Sean asked "what could prevent this next time?".
- **Shell-quoting artifact in Edit 1.** To dodge the chat-quoting problem during the photos schemas, I wrote `single-quote-light-single-quote` literally in the description string. Cleanup attempt with `"light"` (double quotes inside a JSON-shaped double-quoted dict literal) broke the syntax — that's what eventually got fixed with single quotes (`'light'` inside a double-quoted JSON value is legal).
- **Notion API timeouts on every backlog write tonight.** 3 of 6 writes timed out before commit, 3 timed out *after* commit. The verification-before-retry rule (per backlog Tier 2 "docs migration" item) saved us from double-writes. Two backlog updates failed to commit at all: (1) the rewrite of the old systemctl-hang Tier-2 entry → graceful-SIGTERM-handler follow-up; (2) an inbox note pointing at #1. The current state is inconsistent: "Top recent ships" shows the 00:39 cap fix and 00:59 fabrication ship, but the old open Tier-2 entry titled "systemctl restart clawdia hangs" is still present and now stale.

## Honest Meta-Observations

- **Sean called out a behavioral pattern explicitly:** "stop asking me to call it done." New memory rule established (END-OF-SESSION rule, memory #15). Don't suggest wrapping after a ship, even framed as praise. Sean decides when the session ends.
- **Apparent shape-A fabrication observed in Sean's screenshots at ~20:13 EDT.** Clawdia said "Three confirmed `save_memory` tool calls, three confirmed results" while explaining her own past behavior. Need to grep her audit logs around that time to confirm whether the calls actually happened or whether this was a shape-A regression. Not investigated tonight — flagged for next session.
- **Notion friction is now actively blocking work,** not just slowing it. The docs-migration Tier 2 backlog item is gaining urgency: tonight saw 5+ timeouts on writes that would each have been one-line markdown edits if the docs lived in `/opt/clawdia/docs/`.

## Backlog State

**Top recent ships (committed to Notion):**
- 2026-05-16 00:59 UTC — Fabrication-rule shape-B and shape-C coverage
- 2026-05-16 00:39 UTC — systemctl restart hang (systemd-level cap)
- 2026-05-16 00:08 UTC — Apple Photos search + read
**Backlog updates that did NOT commit (timeouts):**
- Rewrite of "systemctl restart clawdia hangs" Tier 2 entry → should now read "Graceful SIGTERM handler in bot_[new.py](http://new.py/)" (the underlying race fix, not the symptom). Old entry still present and now stale.
- Inbox note pointing at the stale entry.
**Newly relevant open items (next-session priorities):**
1. **Graceful SIGTERM handler in bot_**[new.py](http://new.py/) — follow-up to today's systemd-level cap. Install SIGTERM handler that (a) sets shutdown flag, (b) cancels pending asyncio tasks, (c) closes HTTP sessions, (d) flushes logs, (e) commits in-progress SQLite txns. Also add explicit `timeout=` to ~10 bare `requests.*` calls at lines 1254, 1321, 1371, 1430, 1837, 2996, 3013, 3147, 3171, 3245. Tier 2 cleanliness, ~30-60 min.
1. **LaunchAgent Tailscale race** — `com.clawdia.imessage` plist doesn't wait for Tailscale interface up; crash-loops on Mac reboot until manually kicked. Add a Tailscale-up dependency, `ThrottleInterval` retry, or move the bind from `100.77.185.52:8733` to `0.0.0.0:8733` with firewall rules. ~10-15 min.
1. **Google Photos search/browse (Inbox)** — Apple half shipped today; Google Photos still pending.
1. **Audit-logs investigation around 20:13 EDT screenshots** — did Clawdia actually call save_memory 3 times during the "airline programs locked in" exchange, or was that a shape-A fabrication regression? Grep `journalctl -u clawdia --since '2026-05-16 00:10' --until '2026-05-16 00:15'` for AUDIT lines with tool=save_memory.

## Next Session Priorities (suggested order)

1. **First 5 minutes:** retry the two failed Notion backlog updates (the graceful-SIGTERM rewrite + the audit-logs investigation entry). They may go through cleanly on a fresh page state. If they fail again, this is more docs-migration ammo.
1. **Audit-log spot check (~5 min)** for the 20:13 EDT save_memory question. Either confirms shape-A is fixed (and tonight's fabrication-rule ship validated by absence) or surfaces a regression to address.
1. **Pick one:** Graceful SIGTERM handler (Tier 2, ~30-60 min, real correctness work), OR LaunchAgent Tailscale race (~15 min, durability), OR resume docs migration scoping (Tier 2, ~3.5h, biggest long-term win).

## Files / state staged for next session

- `/opt/clawdia/bot_new.py` — 8780 lines, 142 tools, all 3 ships landed
- `/opt/clawdia/clawdia.service.systemd` — copy of live unit for git tracking
- `/etc/systemd/system/clawdia.service` — live unit with `TimeoutStopSec=10` + `KillMode=mixed`
- `/tmp/bot_new.py.pre-photos` — pre-photos backup (this session only)
- `~/Desktop/clawdia_handoff.md` (Mac) — updated bootloader, **needs re-upload to Claude project knowledge**
- `/opt/clawdia/clawdia_handoff.md` (VPS) — same content as Mac copy

## VPS environment

No env changes today. `/etc/clawdia/env` unchanged. Mac bridge env (`CLAWDIA_IMESSAGE_URL`, `CLAWDIA_IMESSAGE_TOKEN`) unchanged and already pointing at the bridge that has photos endpoints.

## One thing to do before this session ends

Upload the new `~/Desktop/clawdia_handoff.md` (97 lines, includes the heredoc transport pattern) to the Claude project's knowledge attachment so next-session-Claude inherits the fix.