# Clawdia Session Handoff — May 15, 2026

<!-- Migrated from Notion 3612e075-ac64-810c-b5a2-f3a88e6682f1 on 2026-05-16. Archived session handoff or historical doc. -->


## Current Status

- **Bot**: active, model `claude-sonnet-4-6`, **tools: 140**, PID changes per restart
- **File**: `/opt/clawdia/bot_new.py` — 8627 lines
- **Last commit**: `a039fd2` (Auto-backup 2026-05-15 20:49 UTC), pushed to GitHub
- **Mac bridge**: 100.77.185.52:8733 — alive with NEW photos endpoints (see below)
- **Logs**: `journalctl -u clawdia -n 50 --no-pager`
- **Backup**: `cd /opt/clawdia && bash backup.sh`

## What Shipped Today (9 substantive items)

1. **Sonnet 4.6 flip-back** (14:48 UTC, commit ea2d468) — Opus 4.7 was temporary swap during yesterday's API outage; restored to Sonnet
1. **Family section in system prompt** (commit bd230b6) — wife Heather + 4 children names hardcoded so Clawdia stops asking who's in the family
1. **FAMILY/family memory dedup** — deleted 12 uppercase-category rows that duplicated lowercase entries from later sessions
1. **Sean DOB dedup** — removed `personal|sean_dob`, kept `personal|dob` (has "Lester" middle name)
1. **Kids' birthdates verified canonical** from birth certificates: Aaron Oct 5 2013, Hailey May 5 2015, Jonah Jun 7 2016, Evan Oct 23 2018. Heather DOB March 29, 1980
1. **Notion read tool real-fix** (commit d36401b) — `notion_read_page` now recurses into column_list, column, toggle, callout, child_page, child_database. Disney World Trip Planner went from "empty" to fully readable (dates, travelers, hotel booking, BWI->MCO, all subpages and databases)
1. **delete_debt_record + list_debt_records tools** (commit 39fdaed) — added with two-phase confirm. Hotfix needed at 19:07 for `default:false` (Python NameError on lowercase JSON boolean)
1. **6 backlog entries tier-assigned** — Apify airfare, systemctl hang (upgraded to Tier 2), topic_name handlers, fabrication-rule generalization, CSP header, URL fetcher
1. **Apple Photos integration on Mac bridge** — see Section "Photos work-in-progress" below

## Photos work-in-progress (PAUSED, ready to finish)

**The hard part is DONE.** OCR decoding works end-to-end, bridge serves real queries through HTTP. The only thing missing is the bot_[new.py](http://new.py/) side (tool schemas + dispatch + prompt update).

### What's complete on the Mac side

- **`/Users/seandurgin/clawdia_imessage_listener.py`** has new functions at lines ~802-997: `_photos_apple_ts_to_iso`, `_photos_iso_to_apple_ts`, `_photos_decode_ocr_blob` (NSKeyedArchiver + LZFSE + ASCII extraction), `_photos_query`, `_photos_file_path`, `photos_search`, `photo_read`
- **HTTP endpoints**: `/photos_search` and `/photo_read` registered in the `do_POST` dispatcher with X-Clawdia-Token auth
- **Dependency installed**: `pyliblzfse` 0.4.1 for system Python 3.9 (`/usr/bin/python3 -m pip install --user pyliblzfse`)
- **Backup**: `/Users/seandurgin/clawdia_imessage_listener.py.bak.before_photos` (md5 `05d4d7a5455de0a7f2513121418f5191`)
- **Verified working**: `curl POST /photos_search` returns real results for date, person, and OCR queries

### What's NOT done on the VPS side

bot_[new.py](http://new.py/) is **clean / untouched** (verified via `git status`). The patcher in `/Users/seandurgin/clawdia_work_in_progress/` failed on Edit 2 (dispatch handler anchor mismatch) and aborted before writing.

### To finish tomorrow

The patcher at `/Users/seandurgin/clawdia_work_in_progress/photos_tools_patcher.py` (with sidecar `photos_anchor.py`) has 5 edits. Edit 2's anchor is wrong — needs to use the actual notes_create dispatch code which is:
```javascript
elif name=="notes_create":
    _title = (inputs.get("title") or "").strip()
    _body = inputs.get("body")
    _folder = inputs.get("folder")
    if not _title:
        return "ERROR: notes_create requires title."
    return await asyncio.to_thread(notes_create, _title, _body, _folder)
```
Note the parens around `inputs.get("title") or ""`, and `_folder = inputs.get("folder")` with no `.strip() or None` chain — my patcher had the wrong shape. One-line fix, then the rest should land cleanly.
After that: scp patcher to VPS, run with `/opt/clawdia/venv/bin/python3`, restart, verify tools=142, test from Telegram with `"Find photos of MADDOX winery from last month"` or `"What screenshots did I take today?"`

### Architecture notes

- **OCR text storage**: Apple wraps OCR text as NSKeyedArchiver (plist) → contains LZFSE-compressed (`bvx2` magic) → Vision framework structures (`CRDocumentOutputRegion` etc). The ASCII-run extraction yields searchable keyword text with some noise (binary metadata leaks). Real text strings ARE recoverable. Words repeat 2-5x in storage — fine for search, ugly for display.
- **Asset → file path**: `{lib_root}/originals/{ZDIRECTORY}/{ZFILENAME}` where ZDIRECTORY is first hex char of UUID
- **Tagged people**: only Sean Durgin (145 faces) and Heather Durgin (93 faces). 3,888 untagged faces — including all 4 kids. Sean should tag them in [Photos.app](http://photos.app/) for face search to work on the kids.
- **iCloud-only photos**: photos with `has_local_file=false` can't be read locally. Sean would need "Download Originals to this Mac" setting.
- **Tool count after ship**: 140 → 142
- **Vision payload kind**: reuses `_kind: "imessage_attachment_payload"` — the dispatcher already handles it (line 5007 of bot_[new.py](http://new.py/))

## Honest engineering failures today

- **Patcher false vs False** (19:05 UTC) — JSON-style `"default":false` in Python dict caused NameError crash-loop, 2-min downtime. AST validation passed; needed import smoke-test.
- **Notion delete operations unreliable** — Inbox cleanup attempt failed multiple ways. Bulk text replacements that span multiple Notion blocks fail more often than adds. Working convention to capture: **Inbox cleanup via Notion UI directly, not API**.
- **Anchor mismatch on photos patcher Edit 2** — wrote anchor from memory instead of from disk. Real code had `(inputs.get("title") or "")` with parens; I wrote `inputs.get("title","")`. One-character difference, wasted 10 min.

## Honest meta-observation

This session ended with 9 ships and one substantive partial. The pattern across the day: dev-Claude consistently overconfident on first-pass diagnoses (Opus pricing claim wrong, _details entries assumed wrong when correct, `false` vs `False` Python-JSON confusion, anchor written from memory). The "Hypotheses not Verdicts" working convention from May 13 is structurally right but slips under conversational momentum. Worth reinforcing each session.

## Inbox state (12 bullets) — needs UI cleanup

All 12 inbox bullets are functionally captured in tier entries added today. Tomorrow's session should:
1. Open Notion Enhancement Backlog in browser
1. Delete the 12 bullets in the Inbox section (Cmd-A across each, Backspace)
1. KEEP the 19:57 Apple Photos entry — that's the one we're partly through, replace it with a richer tiered entry referencing this handoff page

## Next session priorities (suggested)

1. **Finish photos tool ship** (~30 min) — fix one anchor in patcher, run, restart, test. Lowest-friction high-value continuation.
1. **Inbox UI cleanup** (~60 sec via browser)
1. **systemctl restart hang** investigation — Tier 2, fresh diagnostic data still warm in journal (hit twice today around 19:05-19:07)
1. **Decide public LCARS dashboard FINISH vs TEAR DOWN** — architectural question outstanding from yesterday

## Files staged on Mac for next session

- `/Users/seandurgin/clawdia_work_in_progress/photos_tools_patcher.py` — needs Edit 2 anchor fix
- `/Users/seandurgin/clawdia_work_in_progress/photos_anchor.py` — Python repr of notes_create tool schema line
- `/Users/seandurgin/clawdia_imessage_listener.py.bak.before_photos` — restore point if bridge needs rollback

## VPS environment

No env changes today. `/etc/clawdia/env` still holds CLAWDIA_IMESSAGE_URL and CLAWDIA_IMESSAGE_TOKEN — both already correctly pointing at the bridge which has the new photos endpoints. No env update needed tomorrow.