"""ONSR Login Tracker — on-demand read/write tools.

The tracker lives in a single Notion paragraph block on page
3572e075-ac64-8164-83ab-f05243e0d6ea. The block holds the counts
(Goal / Current Count / Remaining / Quarter End) followed by a
Login Log. These tools read and update that block in place, reusing
bot_new's Notion primitives (no new auth, no new API surface).

Mirrors the parse logic in briefing_sources._render_onsr_tracker so
the on-demand answer matches the morning brief exactly.
"""
import re
import datetime
import zoneinfo

ONSR_PAGE_ID = "3572e075-ac64-8164-83ab-f05243e0d6ea"
ONSR_BLOCK_ID = "3572e075-ac64-81e5-8f59-cfc773f2c78c"
EASTERN = zoneinfo.ZoneInfo("America/New_York")


def _fetch_block_text():
    """Return (block_type, full_plain_text) for the tracker block, or (None, err)."""
    import bot_new
    import requests
    try:
        g = requests.get(
            f"{bot_new.NOTION_API}/blocks/{ONSR_BLOCK_ID}",
            headers=bot_new.NOTION_HEADERS, timeout=15,
        )
        if not g.ok:
            return None, f"Notion fetch error {g.status_code}: {g.text[:200]}"
        block = g.json()
        bt = block.get("type")
        rich = block.get(bt, {}).get("rich_text", [])
        full = "".join(x.get("plain_text", "") for x in rich)
        return bt, full
    except Exception as e:
        return None, f"ONSR block fetch failed: {e}"


def _parse(full):
    """Pull goal/current/remaining/quarter_end out of the block text."""
    goal = current = remaining = quarter_end = None
    for line in full.replace("<br>", "\n").split("\n"):
        s = line.strip()
        low = s.lower()
        if low.startswith("goal:"):
            for tok in s.split(":", 1)[1].split():
                if tok.isdigit():
                    goal = int(tok)
                    break
        elif low.startswith("current count:"):
            try:
                current = int(s.split(":", 1)[1].strip().split()[0])
            except Exception:
                pass
        elif low.startswith("remaining:"):
            try:
                remaining = int(s.split(":", 1)[1].strip().split()[0])
            except Exception:
                pass
        elif low.startswith("quarter end:"):
            try:
                quarter_end = datetime.datetime.strptime(
                    s.split(":", 1)[1].strip(), "%B %d, %Y"
                ).date()
            except Exception:
                pass
    return goal, current, remaining, quarter_end


def _rollup(goal, current, remaining, quarter_end):
    if remaining is None and goal is not None and current is not None:
        remaining = max(0, goal - current)
    msg = f"{current}/{goal} logins ({remaining} remaining)"
    if quarter_end:
        today = datetime.datetime.now(EASTERN).date()
        days_left = (quarter_end - today).days
        if days_left < 0:
            msg += f" — quarter ended {-days_left}d ago"
        elif days_left == 0:
            msg += " — TODAY is the deadline"
        else:
            msg += f" — {days_left}d until {quarter_end.strftime('%b %d')}"
            weekday = today.weekday()
            if 1 <= weekday <= 4 and remaining > 0:
                workdays_left = max(1, int(days_left * 5 / 7))
                if remaining / workdays_left > 1.0:
                    msg += " ⚠️ Behind pace — log today"
                else:
                    msg += " ✓ On pace"
    return msg


def onsr_status():
    """Read the current ONSR login count and progress toward the quarterly goal."""
    bt, full = _fetch_block_text()
    if bt is None:
        return full  # error string
    goal, current, remaining, quarter_end = _parse(full)
    if current is None or goal is None:
        return ("ONSR tracker page found but counts couldn't be parsed "
                "(expected 'Goal:' and 'Current Count:' lines).")
    return "📊 ONSR Login Tracker: " + _rollup(goal, current, remaining, quarter_end)


def _write_count(new_count, append_log_dates=None):
    """Set Current Count to new_count, recompute Remaining, optionally append
    dated entries to the Login Log. Rewrites the whole tracker block."""
    import bot_new
    bt, full = _fetch_block_text()
    if bt is None:
        return None, full
    goal, old_current, _old_remaining, quarter_end = _parse(full)
    if goal is None:
        return None, "ONSR tracker malformed (no Goal line); refusing to write."
    new_remaining = max(0, goal - new_count)

    # Replace the Current Count: line
    full2 = re.sub(r"(?im)^current count:.*$", f"Current Count: {new_count}", full)
    # Replace the Remaining: line
    full2 = re.sub(r"(?im)^remaining:.*$", f"Remaining: {new_remaining}", full2)

    # Optionally append dated login entries, continuing the numbering
    if append_log_dates:
        # Find the highest existing log number
        nums = [int(m.group(1)) for m in re.finditer(r"(?m)^(\d+)\.\s", full2)]
        next_n = (max(nums) + 1) if nums else 1
        additions = []
        for d in append_log_dates:
            additions.append(f"{next_n}. logged {d}")
            next_n += 1
        full2 = full2.rstrip() + "\n" + "\n".join(additions)

    res = bot_new.notion_update_block(ONSR_BLOCK_ID, full2)
    if not res.startswith("Updated"):
        return None, f"Write failed: {res}"
    return (old_current, new_count), None


def onsr_set(count):
    """Set the ONSR login count to an absolute value (catch-up / correction).
    Recomputes Remaining. Returns old->new for confirmation."""
    try:
        new_count = int(count)
    except Exception:
        return f"Invalid count '{count}' — must be a whole number."
    if new_count < 0:
        return "Count can't be negative."
    res, err = _write_count(new_count)
    if err:
        return err
    old, new = res
    bt, full = _fetch_block_text()
    goal, cur, rem, qe = _parse(full)
    return (f"✅ ONSR count set: {old} → {new}.\n"
            + "📊 " + _rollup(goal, cur, rem, qe))


def onsr_log(n=1):
    """Log N new ONSR login(s) — increments the count by N (default 1),
    appends dated entries to the Login Log, recomputes Remaining."""
    try:
        n = int(n)
    except Exception:
        n = 1
    if n < 1:
        n = 1
    bt, full = _fetch_block_text()
    if bt is None:
        return full
    goal, current, remaining, quarter_end = _parse(full)
    if current is None:
        return "Couldn't read current ONSR count; aborting log."
    today = datetime.datetime.now(EASTERN).date().isoformat()
    res, err = _write_count(current + n, append_log_dates=[today] * n)
    if err:
        return err
    old, new = res
    bt2, full2 = _fetch_block_text()
    g2, c2, r2, q2 = _parse(full2)
    label = "login" if n == 1 else "logins"
    return (f"✅ Logged {n} ONSR {label}: {old} → {new}.\n"
            + "📊 " + _rollup(g2, c2, r2, q2))
