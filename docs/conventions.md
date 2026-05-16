# 📋 Claude's Working Conventions for Clawdia Sessions

<!-- Migrated from Notion 3522e075-ac64-8147-8629-c264aafc90e6 on 2026-05-16. Source of truth lives in this file going forward. -->

Standing rules for how Claude (the chat assistant who builds and maintains Clawdia's code) works with Sean. These are durable conventions that should be re-read at the start of every session. Last updated 2026-05-07.

# How to use this page

Future Claude: read this at the start of any Clawdia build session, alongside the Shared Changelog. These rules came from real friction — they exist because Sean noticed something that wasn't working and we agreed to fix it. Don't argue with them; just follow them. If a new rule needs to be added, add it here AND log a brief note in the Shared Changelog so the next session sees the change.

# 🧰 Step 0 — Tool loadout check (do this FIRST)

**Rule:** Before committing to ship anything, list your available tools and verify the ones this session will need. Tool availability per Claude session is unreliable — sometimes Claude Desktop only loads a subset (e.g. only the 8 Notion tools without `Macos:Shell` or `Desktop Commander:start_process`), and you cannot ship a Clawdia code change without shell access to the droplet.
**Required for Clawdia build sessions:**
- `Macos:Shell` OR `Desktop Commander:start_process` — needed to SSH `root@209.38.49.104` for code edits, restarts, and verification
- `Notion:notion-fetch`, `notion-search`, `notion-update-page` — needed to read the Shared Changelog and Working Conventions, and to log entries
- For tasks involving Sean's personal data: `Read and Send iMessages:*` (sometimes loaded, sometimes not)
**Verification preamble (paste this as your first internal check):**
> Before starting, list my tools. Confirm I have:
> 1. A way to run shell commands on Sean's Mac (Macos:Shell or Desktop Commander start_process)
> 2. Notion read+write tools (fetch, search, update-page)
> If either is missing, STOP and tell Sean — he'll need to enable the relevant extension in Claude Desktop and restart, OR we'll have to do this through copy-paste with him as the executor.
**If tools are missing:** do NOT silently work around them by writing patch scripts and asking Sean to paste them. That pattern caused real friction on 2026-05-01 — two parallel Claude sessions ended up driving Clawdia changes (one via SSH, one via Telegram-mediated copy-paste) and produced duplicate `notion_add_song_idea` rows, schemas, and dispatcher entries from a double-run of the same patch. **One driver per session, period.** Either you have the tools to ship, or you defer to a session that does — never run shadow-builds in parallel with another active session.
**Why the variability exists:** Claude Desktop loads tools at session start based on which extensions are enabled and (apparently) some opaque routing logic. Long conversations also lose tools — `web_search` and `Macos:Shell` got dropped from a session on 2026-05-01 after enough turns. If a tool is missing in a long session, restarting Claude Desktop and starting fresh usually restores it.
Confirmed: 2026-05-01

# 🚫 Don't heredoc-via-SSH-via-zsh for multi-line patches

**Rule:** When shipping multi-line Python patch scripts to the droplet, write the script to a local file FIRST, then `scp` it to the droplet, then execute it as a separate step. Do NOT try to chain `ssh root@droplet 'cat > /tmp/x.py' << 'EOF' ... EOF` from your local shell — the heredoc termination interacts badly with `Macos:Shell`'s timeout and wedges the MCP server. This happened three times on 2026-05-01.
**Bad** (wedges the MCP):
```bash
ssh root@209.38.49.104 'cat > /tmp/patch.py' << 'PYEOF'
import re
# ... 100 lines ...
PYEOF
```
**Good** (separate steps):
```bash
# Step 1: write locally on Mac
cat > /tmp/patch.py <<'PYEOF'
# ... script ...
PYEOF
# Step 2: scp
scp /tmp/patch.py root@209.38.49.104:/tmp/
# Step 3: execute remotely
ssh root@209.38.49.104 'python3 /tmp/patch.py'
```
**Even better** (single-quoted ssh with `python3 -c`) for tiny patches under ~30 lines:
```bash
ssh root@209.38.49.104 "python3 -c 'import re; ... small inline script ...'"
```
**Why this matters:** when `Macos:Shell` wedges, you lose access to the entire SSH path and have to ask Sean to restart Claude Desktop. That kills momentum and wastes context. The scp-first pattern is slightly more typing but always works.
Confirmed: 2026-05-01

# 🔐 Secret handling

**Rule:** When provisioning any API key, token, or credential to the Clawdia droplet (or anywhere else), ALWAYS use silent input — never put the literal value in a chat message, command-line argument, or anywhere it would be echoed or logged.
**The pattern:**
```bash
read -s -p "Paste key: " VAR
echo "" # newline after silent input
echo "export APIFY_API_TOKEN='$VAR'" >> /etc/clawdia/env
```
**Why:** A key got leaked into chat once and had to be rotated. Telegram's httpx logger also leaked the bot token via URL paths until that got muzzled. Treat every secret as if it will be logged somewhere you didn't anticipate, and don't give the logger anything to capture.
Confirmed: 2026-04-29

# 🚀 Ship-and-demo rule

**Rule:** When shipping a new Clawdia tool or feature, end the session with ONE concrete copy-paste-able test command Sean can paste directly into Telegram. NOT abstract example phrasings.
**Bad** (abstract):
> *"You can ask her to watch for milwaukee m18 batteries under $100"*
**Good** (concrete, copy-paste-able):
> Paste this into Telegram exactly:
> > Search Marketplace for milwaukee m18 batteries under $100
The test command should exercise the new code path end-to-end through the actual user surface (Telegram), not via SSH or Python REPL. The point is: Sean shouldn't have to figure out how to invoke what was just built. If you can't write a one-line test command, the feature isn't ready to hand off.
**Why:** Apify shipped without clear usage instructions. Sean asked "how was I supposed to initiate the apify?" — fair question, my failure.
Confirmed: 2026-04-30

# 🛠 Code editing patterns


## Always backup before mutating bot_[new.py](http://new.py/)

```bash
cp bot_new.py bot_new.py.bak-$(date +%Y%m%d-%H%M%S)
```
Keeps a rolling history of recoverable states. Disk is cheap.

## Surgical edits via Python heredoc, not sed

For any patch to bot_[new.py](http://new.py/), write a Python script with explicit `assert s.count(old) == 1` checks, scp it to the droplet, run it, verify SYNTAX OK, restart. This catches off-by-one matches before they corrupt the file. Sed is too easy to get wrong on multi-line changes.

## Schemas are single-line dicts

Tool schemas in bot_[new.py](http://new.py/) are written as single-line Python dicts (not pretty-printed) for grep-friendliness. Match the existing style when adding new tools.

## JSON true/false vs Python True/False — bitten twice now

Tool schemas in `bot_new.py` are Python dicts, NOT JSON. The dict gets serialized to JSON when sent to the Anthropic API, but at parse time Python rules apply.
**Specifically:** booleans must be `True`/`False` (capital), not `true`/`false` (lowercase). Shipped lowercase twice — once on `gemini_generate_image` (caught quickly), once on `web_price_check` (crash-looped the service 6 times before noticed). Both times a real `import bot_new` after the patch would have caught it instantly.
**Rule:** Whenever a schema includes `"default":` followed by a boolean, double-check it's capital `True` or `False`. AST parse alone won't catch this — it's a NameError at module import, not a syntax error.
**Better rule:** after any patch to bot_[new.py](http://new.py/), confirm `systemctl is-active clawdia` says `active`, not `activating` (which means crash-looping). If it says activating, immediately tail journalctl for the traceback.
Confirmed: 2026-04-30

## When two AIs share state — coordinate via canonical-owner pattern

When both Clawdia (runtime) and dev-Claude can write to the same SQLite tables (memory.db debt_accounts, net_worth_assets, etc.), there is a real risk of duplicate rows when each side picks slightly different `account_id` strings for the same logical entity. Happened on 2026-04-30 with debt_tracking: dev-Claude inserted `chase_amazon_prime` and `apg_l3002`; Clawdia later inserted `chase_amazon` and `apg_auto`. Both correct, both upsert-idempotent, but they created duplicate rows because the IDs differed.
**Convention going forward:** for any shared SQLite state in /var/lib/clawdia/memory.db, **Clawdia is the canonical owner**. Dev-Claude only writes during initial seeding, bug fixes, or schema migrations — not for routine data updates. Sean tells Clawdia about new statements, balances, and APRs; Clawdia handles them via tools.
Dev-Claude DOES still write to:
- New schema migrations
- Seeding fresh tables that have no Clawdia-side write path yet
- Cleanup operations (deleting duplicates, fixing data corruption)
- Debugging where reproducing via Telegram is impractical

## create_google_sheet "multi-tab dropping" — NOT a tool bug

Observed 2026-04-30: Clawdia repeatedly told Sean "create_google_sheet is dropping the tabs parameter mid-session" and produced multiple separate sheets when Sean asked for one multi-tab sheet. Initially diagnosed as JSON-string serialization issue, partial fix shipped (string→list coercion at dispatcher).
**Real root cause (verified via debug logs at 21****:03-21:****04 UTC):** Clawdia is structuring her LLM tool calls as multiple separate `create_google_sheet` invocations — one per logical sheet — rather than batching them into one call with `tabs=[tab1, tab2]`. The tool itself works perfectly; each individual call creates exactly the tab(s) she sends. She then surfaces this to Sean as "the tool is broken" because the user-visible result (multiple separate sheets requiring manual merge) doesn't match the user's request (one multi-tab sheet).
The debug logs ALSO show a separate behavior: she sometimes makes a probing call with `tabs=[]` first, gets the ERROR back, then retries with content. Harmless but wasteful.
**Fix:** system prompt clarification that create_google_sheet expects ALL tabs in a single call, not one call per tab. Not a code change. Lower priority than other items but worth doing eventually.

## Dispatcher uses `name=="x"` no spaces

The run_tool dispatcher uses `elif name=="tool_name":` with no spaces around `==`. Match this when adding tools.

## Defensive .get() not [] for ALL dispatcher inputs

Never `inputs["required_field"]` — always `inputs.get("required_field", "").strip()` followed by an early-return ERROR string if empty. Anthropic doesn't enforce schema `required` at the API level, so the model CAN call a tool without all fields populated. Defensive `.get()` returns a clean error; bracket access raises KeyError which surfaces to the user as `Something went wrong: 'field_name'`.

## Always verify after restart

```bash
systemctl restart clawdia
sleep 6
systemctl is-active clawdia
journalctl -u clawdia --since '20 seconds ago' | grep -E 'Starting|tools:|ERROR|Traceback'
```
Look for `Starting Clawdia (model: ..., tools: N)` and zero Tracebacks. If the tool count didn't go up after adding a tool, the patch didn't land where you thought it did.

# 📝 Notion idiosyncrasies


## Filename auto-linkification

Notion auto-converts `bot_new.py` to `bot_[new.py](http://new.py)` in rendered markdown. When writing `update_content` calls that anchor on text containing `.py` filenames, you have to match the linkified form, not the plain form. Failed update calls usually mean you wrote `bot_new.py` but the page contains the linkified version. Same for `briefing.py`.

## Bullets use literal `[ ]` and `[x]`

The Enhancement Backlog uses `[ ]` for open items and `[x]` for done. Some bullets render with backslash escapes as `\[ \]` — match the actual rendering by fetching the page first if a search-and-replace fails.

## Emoji in briefings

Use literal UTF-8 emoji bytes (✅, 🎵), not Python `\U` escapes. The [briefing.py](http://briefing.py/) file already uses literal bytes throughout; match that.

# 🔍 Look harder before claiming "broken" or "half-shipped"

**Rule:** When something looks broken, missing, or half-shipped, do at least 3 distinct verification checks before declaring it so. Fast verdicts on incomplete evidence cause real waste — you'll either rebuild something that already exists or accuse Clawdia of fabricating when the bug is in YOUR understanding.
**Three real cases from 2026-05-06 evening that all looked like fabrication or half-shipped state but weren't:**
1. **"Clawdia is fabricating tool errors" (Oracle attachment).** Looked like a fabrication — turned out to be a real volatile-attachment-id bug in the dispatcher code. Required adding diagnostic logging to capture actual tool input/output before the truth surfaced.
1. **"****`plaid_recurring`**** schema lies to the model about a non-existent function."** Looked like a half-shipped item — turned out the function existed in its own module file (`plaid_recurring.py`) under the name `format_recurring_summary`, not in `plaid_finance.py` under `get_recurring`. The Architecture doc was correct; my grep was too narrow.
1. **"3 'Safety Boot Comparison' duplicates — trash 2 of them."** Looked like duplicates worth deleting — they were duplicates worth ARCHIVING (recoverable) not trashing, and a quick fetch-and-diff before deletion confirmed the most-detailed one was the keeper.
**Verification checks before declaring something broken/missing/half-shipped:**
1. Grep across ALL `.py` files in `/opt/clawdia/`, not just the obvious one
1. Check the actual dispatcher branch (might be a different function name than expected)
1. If a separate module is mentioned in any doc, `ls` it before assuming the doc is wrong
1. For tool fabrication suspicions: add diagnostic logging at the dispatcher layer to capture real inputs/outputs
1. For "duplicate" pages/files: read both to confirm content overlap, don't assume from titles alone
**The cost asymmetry:** spending 5 extra minutes verifying costs nothing. Acting on a wrong verdict costs an hour of rebuild + an apology + drift in canonical docs.
Confirmed: 2026-05-06

# 🎯 Build to use cases, not to APIs

**Rule:** When integrating with an external API (Gmail, Drive, Plaid, Notion, etc.), DO NOT speculatively pre-build every endpoint the API exposes. Build only the tools that close a real workflow Sean has actually surfaced. The right batching unit is "a use case," not "an API."
**Why this is right, not lazy:**
1. **APIs are huge; use cases are narrow.** Gmail API has ~50 endpoints across messages/threads/labels/filters/drafts/settings/history/etc. Sean uses a fraction of those. Building all of them would have been 2-3x the work for capabilities he might never use (`messages.import`, `delegates.create`, `forwarding_addresses.create`, etc.).
1. **Each tool needs a thoughtful schema description.** The schema is what the LLM uses to decide WHEN to call the tool. Generic "wraps users.drafts.create" descriptions lead to bad routing decisions. Schemas with judgment in them — like "prefer over gmail_send for job applications" — only emerge from real use, not from API docs.
1. **System prompt has a context budget.** Every tool eats tokens on every API call. 124 tools is already a lot of context overhead per turn. Adding 30 more "just in case" tools would slow every interaction with no benefit.
1. **Each tool needs a security/safety story.** `gmail_trash` requires confirmation. `drive_edit_docx` returns ERROR for Google Docs. `reminders_add` distinguishes itself from `notion_add_todo`. That nuance comes from understanding the actual use case, not from API docs.
**The right batching unit is the use case.** When a real workflow surfaces, batch all the tools that close that workflow:
- 2026-05-06 evening: Gmail organize/maintain shipped 7 logical tools / 14 schemas in one go because Sean had a single coherent need ("clean up my inbox") that genuinely needed all of them.
- 2026-05-06 night: Three phases (drafts + attachments + .docx edit) shipped in one session because they collectively close the resume use case end-to-end. Phase 1 alone wouldn't have closed it; building all of Gmail wouldn't have been justified.
- 2026-05-04: iMessage read shipped 3 tools at once (unread/search/recent) because they share infrastructure and Sean needed all three to triage messages.
**The wrong pattern:** noticing three open Gmail items in the backlog and proposing to ship them in one go before any has a real triggering use case. Building to the API surface, not to Sean's actual workflow, produces tools whose schemas are guesses about how they should be used.
**Contrast with platform integrations (Zapier, IFTTT):** big platforms expose all of an API because they don't know which user wants which feature. Clawdia is single-user and use-case-driven, so the incremental pattern is correct. The "small bites" feel like overhead because each ship is small, but the cumulative result (124 tools, each with a real reason to exist) is leaner and more useful than a 200-tool fully-mapped surface would be.
**How to apply:**
- When Sean asks for something the current tool surface can't do, ask: *what use case does this close?* Build to that, not beyond.
- When you find a backlog item with no triggering use case, leave it open — don't preemptively ship it.
- When you DO ship multiple tools at once, name the use case in the commit/changelog so future-Claude knows why they were batched.
Confirmed: 2026-05-07

# 🧠 Memory notes

The `userMemories` block in Claude's context contains durable facts about Sean (address, military service, dental insurance, etc.) that follow him across all chat sessions. Standing rules that ALSO follow him across all chats live in user memory edits (visible via the memory tool). This page is for rules specific to **Clawdia work sessions** — anything that's about how to build/maintain the bot, not about Sean as a person.
If a rule applies to ALL Claude interactions with Sean (not just Clawdia work), put it in user memory. If it's only relevant when building Clawdia, put it here.

# 🔄 The session loop

A typical Clawdia build session looks like:
1. **Read the Shared Changelog** (page ID `34c2e075-ac64-810d-936b-de7847c8e073`) for recent state changes
1. **Listen to what Sean wants** — usually a tool to add, a bug to fix, or auth to rotate
1. **Backup, code, ship, verify** using the patterns above
1. **Test end-to-end via Telegram** if the change touches user-visible behavior
1. **Log to Shared Changelog** with the standard format: `[YYYY-MM-DD HH:MM ET] [claude] [scope] - what - why - links`
1. **Update the Backlog** if relevant (mark done, add new items)
1. **Hand off with a copy-paste-able test command** (see Ship-and-demo rule above)

# 📜 History

- 2026-05-07: Added 'Build to use cases, not to APIs' rule. Codifies the incremental pattern that's been working organically — build only tools that close real workflows Sean has surfaced, batch by use case (not by API surface), don't speculatively pre-build entire APIs.
- 2026-05-06: Added 'Look harder before claiming broken/half-shipped' rule above. Three real misdiagnoses in one session (volatile-attachment-id, plaid_recurring "missing", boot-page "trash these") drove the lesson home.
- 2026-05-01: Added 'To-do system lives in Notion databases' rule below.
- 2026-04-30: Page created. Seeded with secret-handling rule and ship-and-demo rule.

# 📋 To-do system lives in Notion databases (not OneNote)

**Rule:** Sean's canonical task management lives in two Notion databases under the page `Sean's HQ` (id `3532e075-ac64-81f6-afbb-cb314763ba07`):
- **Sean's To-Do** — data source id `2692e075-ac64-80e3-9454-000bf68150c9`. Actionable items. Schema: Task name (title), Status (Not started / In progress / Done), Priority (Now / This week / Someday), Category (Personal / Work / Family / Music / Clawdia / Truck / Home / Finance), Due date, Notes, Assignee.
- **Sean's Research & Backlog** — data source id `0b6392cd-2285-4969-a499-0182e4eafe45`. Things to investigate or decide on. Schema: Topic (title), Status (Active / Decided / Parked), Category (same options), Notes, Outcome, Date added.
**OneNote is no longer Sean's daily task tracker.** The morning briefing should NOT scrape OneNote `Daily To Do` pages — doing so surfaces stale content like "follow up with Heather about the dishwasher" that has no business in a daily briefing. OneNote is reserved for graduated "program of record" content (multi-step projects with their own structure), not day-to-day tasks.
**When building or modifying the briefing:**
- Pull active to-dos from `Sean's To-Do` (filter Status != 'Done').
- Pull active research from `Sean's Research & Backlog` (filter Status = 'Active').
- Do not include OneNote `Daily To Do` content in the briefing template.
**When Sean says "add to my to-do list" or "add to research":**
- Add a row to the appropriate database via the Notion API.
- Default Priority = 'This week' for to-dos unless Sean specifies.
- Default Status = 'Not started' for to-dos and 'Active' for research.
- Always populate Category if it's clear from context; ask if it's ambiguous.
**Why this rule exists:** OneNote was the legacy task home. Notion databases are queryable, filterable, and deduplicate cleanly. OneNote pages are free text — stale items linger forever and Clawdia couldn't tell which were current. Migrated 2026-05-01.
Confirmed: 2026-05-01