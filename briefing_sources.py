"""Watched sources for the morning briefing.

Each source pulls live content from an external place (Apple Note via Mac bridge,
Notion page via API) and renders a short section for inclusion in the briefing.

Failures are isolated: a broken source returns None and the briefing skips it,
never crashes. Logs warnings so we know something's off.
"""
import logging
import os
from datetime import datetime, date
from zoneinfo import ZoneInfo

log = logging.getLogger("clawdia.briefing.sources")

EASTERN = ZoneInfo("America/New_York")


# --- Source fetchers -------------------------------------------------------

def _fetch_apple_note(note_title):
    """Look up an Apple Note by title, return its body text (or None on failure).

    Uses the same Mac-bridge architecture as notes_search/notes_read in bot_new.py.
    """
    try:
        import requests
        url = os.environ.get("CLAWDIA_IMESSAGE_URL", "")
        token = os.environ.get("CLAWDIA_IMESSAGE_TOKEN", "")
        if not url or not token:
            log.warning("apple_note: bridge env not set; skipping")
            return None
        # Search for the note
        r = requests.post(
            url + "/notes_search",
            headers={"X-Clawdia-Token": token, "Content-Type": "application/json"},
            json={"query": note_title, "max_results": 5},
            timeout=15,
        )
        if r.status_code != 200:
            log.warning("apple_note search HTTP %d: %s", r.status_code, r.text[:120])
            return None
        notes = r.json().get("notes", [])
        # Prefer exact title match
        target = None
        for n in notes:
            if n.get("title", "").strip().lower() == note_title.strip().lower():
                target = n
                break
        if not target and notes:
            target = notes[0]  # best-effort fallback
        if not target:
            log.info("apple_note: no match for %r", note_title)
            return None
        # Read full body
        r = requests.post(
            url + "/notes_read",
            headers={"X-Clawdia-Token": token, "Content-Type": "application/json"},
            json={"note_id": target["id"]},
            timeout=15,
        )
        if r.status_code != 200:
            log.warning("apple_note read HTTP %d", r.status_code)
            return None
        return r.json().get("note", {}).get("body", "") or None
    except Exception as e:
        log.warning("apple_note %r failed: %s", note_title, e)
        return None


def _fetch_notion_page_text(page_id, notion_read_fn):
    """Fetch Notion page body via the existing notion_read_page tool. Returns plain text or None."""
    try:
        if not notion_read_fn:
            return None
        result = notion_read_fn(page_id)
        if not isinstance(result, str) or not result.strip():
            return None
        return result
    except Exception as e:
        log.warning("notion_page %s failed: %s", page_id, e)
        return None


# --- Renderers -------------------------------------------------------------

def _render_house_projects(body_text):
    """Strip the H1 title that Apple Notes prepends, return remaining lines as bullets."""
    if not body_text:
        return None
    lines = [ln.strip() for ln in body_text.split("\n") if ln.strip()]
    # First line is usually the title (matches the note title). Drop if so.
    if lines and lines[0].lower().startswith("house project"):
        lines = lines[1:]
    if not lines:
        return None
    # Bullet each line
    bulleted = "\n".join(f"  • {ln}" for ln in lines)
    return bulleted


def _render_onsr_tracker(body_text):
    """Extract Goal / Current / Remaining / Quarter End from the tracker page,
    surface a one-line rollup with days-until-deadline math.
    """
    if not body_text:
        return None
    # The page text uses '<br>' separators in some renderings, real newlines in others.
    # Normalize.
    txt = body_text.replace("<br>", "\n")
    goal = current = remaining = quarter_end = None
    for line in txt.split("\n"):
        line = line.strip()
        if line.lower().startswith("goal:"):
            # "Goal: 26 logins by May 31, 2026"
            try:
                goal_part = line.split(":", 1)[1].strip()
                # Extract the first number
                for tok in goal_part.split():
                    if tok.isdigit():
                        goal = int(tok)
                        break
            except Exception:
                pass
        elif line.lower().startswith("current count:"):
            try:
                current = int(line.split(":", 1)[1].strip().split()[0])
            except Exception:
                pass
        elif line.lower().startswith("remaining:"):
            try:
                remaining = int(line.split(":", 1)[1].strip().split()[0])
            except Exception:
                pass
        elif line.lower().startswith("quarter end:"):
            try:
                # "Quarter End: May 31, 2026"
                date_part = line.split(":", 1)[1].strip()
                quarter_end = datetime.strptime(date_part, "%B %d, %Y").date()
            except Exception:
                pass

    if current is None or goal is None:
        return None  # malformed page, skip silently

    if remaining is None:
        remaining = max(0, goal - current)

    today = datetime.now(EASTERN).date()
    days_left = (quarter_end - today).days if quarter_end else None

    rollup = f"{current}/{goal} logins ({remaining} remaining)"
    if days_left is not None:
        if days_left < 0:
            rollup += f" — quarter ended {-days_left}d ago"
        elif days_left == 0:
            rollup += " — TODAY is the deadline"
        else:
            rollup += f" — {days_left}d until {quarter_end.strftime('%b %d')}"
            # Workday nudge: Tue-Fri reminder if behind pace
            weekday = today.weekday()  # Mon=0 .. Sun=6
            if 1 <= weekday <= 4 and remaining > 0 and days_left > 0:
                # Rough pace: if remaining per workday > 1, flag
                # Workdays remaining = approx days_left * 5/7
                workdays_left = max(1, int(days_left * 5 / 7))
                if remaining / workdays_left > 1.0:
                    rollup += "\n  ⚠️ Behind pace — log today"
                else:
                    rollup += "\n  ✓ Log today (workday)"
    return f"  • {rollup}"


# --- Watched sources config ------------------------------------------------

WATCHED_SOURCES = [
    {
        "key": "house_projects",
        "header": "🔨 *House Projects*",
        "type": "apple_note",
        "source": "House Projects",
        "renderer": _render_house_projects,
    },
    {
        "key": "onsr_tracker",
        "header": "📊 *ONSR Login Tracker*",
        "type": "notion_page",
        "source": "3572e075-ac64-8164-83ab-f05243e0d6ea",
        "renderer": _render_onsr_tracker,
    },
]


# --- Public entry point ----------------------------------------------------

def render_watched_sources(notion_read_fn=None):
    """Iterate WATCHED_SOURCES, fetch each, render to a section. Return list of
    formatted section strings (each already includes its header). Sources that
    fail or return empty are silently omitted."""
    sections = []
    for src in WATCHED_SOURCES:
        try:
            if src["type"] == "apple_note":
                body = _fetch_apple_note(src["source"])
            elif src["type"] == "notion_page":
                body = _fetch_notion_page_text(src["source"], notion_read_fn)
            else:
                log.warning("unknown source type %r", src["type"])
                continue
            rendered = src["renderer"](body)
            if rendered:
                sections.append(f"{src['header']}\n{rendered}")
            else:
                log.info("source %s returned empty; skipping", src["key"])
        except Exception as e:
            log.warning("source %s failed: %s", src.get("key", "?"), e)
            continue
    return sections
