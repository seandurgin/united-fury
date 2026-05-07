"""Clawdia Family Dashboard.

Tailnet-only HTTP server bound to 100.122.55.112:8090. No auth — Tailscale is the gate.
Renders 5 cards: Money, House Projects, ONSR Login Tracker, Notion To-Dos, Recent Notes.

Reuses bot_new.py functions for data access. Auto-refresh every 5 min.
"""
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from jinja2 import Template

# Ensure we can import bot_new
sys.path.insert(0, "/opt/clawdia")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("clawdia.dashboard")

EASTERN = ZoneInfo("America/New_York")

import re as _re

KIDS_SAVINGS_RE = _re.compile(r"^\s*(AARON|HAILEY|JONAH|EVAN)\s+SAVINGS\s+\(\.\.\.\d+\):\s*\$([\d,]+\.?\d*)\s*\[savings\]\s*$", _re.IGNORECASE)


def _rollup_kids_savings(text):
    """Replace 4 kids' savings lines (AARON/HAILEY/JONAH/EVAN) with one rolled-up line.

    Returns the transformed text. Idempotent: if no kids savings lines found,
    text is returned unchanged.
    """
    if not isinstance(text, str):
        return text
    lines = text.split("\n")
    out = []
    kids_total = 0.0
    kids_count = 0
    rollup_inserted = False
    for line in lines:
        m = KIDS_SAVINGS_RE.match(line)
        if m:
            kids_count += 1
            try:
                kids_total += float(m.group(2).replace(",", ""))
            except ValueError:
                pass
            # Replace first kid's-savings line with the rollup; drop subsequent ones
            if not rollup_inserted:
                rollup_inserted = True
                # Defer adding rollup until we know the final total — emit a placeholder
                out.append("__KIDS_ROLLUP_PLACEHOLDER__")
            continue
        out.append(line)
    if kids_count > 0:
        rollup_line = f"  Kids savings (Aaron, Hailey, Jonah, Evan): ${kids_total:,.2f} across {kids_count} accounts"
        out = [rollup_line if line == "__KIDS_ROLLUP_PLACEHOLDER__" else line for line in out]
    return "\n".join(out)


def _split_totals_from_balances(text):
    """Split a balances block into (main_text, totals_text). Totals are the last
    lines starting with 'Total ' or 'Net:'. Returns (main_only, totals_only_or_None).
    """
    if not isinstance(text, str):
        return text, None
    lines = text.split("\n")
    # Walk backward, collecting trailing total-style lines
    totals = []
    i = len(lines) - 1
    while i >= 0:
        stripped = lines[i].strip()
        if stripped.startswith(("Total ", "Net:")) or stripped == "":
            totals.insert(0, lines[i])
            i -= 1
        else:
            break
    if not any(l.strip().startswith(("Total ", "Net:")) for l in totals):
        return text, None
    main = "\n".join(lines[:i+1]).rstrip()
    totals_text = "\n".join(totals).strip()
    return main, totals_text


app = FastAPI(title="Clawdia Family Dashboard")


# --- Card renderers -------------------------------------------------------

def render_money_card():
    """Plaid balances + recent transactions + net worth + upcoming bills.

    Returns a dict: {balances_main, totals, net_worth, upcoming, refreshed}.
    Kids' savings (Aaron, Hailey, Jonah, Evan) rolled up into one line.
    Totals split out so the template can hide them behind a toggle.
    """
    result = {"balances_main": None, "totals": None, "net_worth": None, "upcoming": None, "refreshed": None}
    try:
        from plaid_finance import get_accounts
        accts = get_accounts()
        if isinstance(accts, str):
            # Roll up kids savings, then split totals from main
            accts = _rollup_kids_savings(accts)
            main, totals = _split_totals_from_balances(accts)
            result["balances_main"] = main
            result["totals"] = totals
        else:
            result["balances_main"] = str(accts)
    except Exception as e:
        log.warning("money: plaid_accounts failed: %s", e)
        result["balances_main"] = f"(plaid_accounts failed: {e})"

    try:
        import bot_new
        nw = bot_new.net_worth() if hasattr(bot_new, "net_worth") else None
        if nw:
            result["net_worth"] = nw
    except Exception as e:
        log.warning("money: net_worth failed: %s", e)

    try:
        import plaid_recurring
        upcoming = plaid_recurring.upcoming_bills_summary(days=14) if hasattr(plaid_recurring, "upcoming_bills_summary") else None
        if upcoming:
            result["upcoming"] = upcoming
    except Exception as e:
        log.info("money: upcoming bills not available: %s", e)

    result["refreshed"] = datetime.now(EASTERN).strftime("%-I:%M %p")
    return result


def render_house_projects_card():
    try:
        import briefing_sources
        body = briefing_sources._fetch_apple_note("House Projects")
        if not body:
            return None
        return briefing_sources._render_house_projects(body)
    except Exception as e:
        log.warning("house_projects failed: %s", e)
        return f"(error: {e})"


def render_onsr_card():
    try:
        import briefing_sources, bot_new
        body = briefing_sources._fetch_notion_page_text(
            "3572e075-ac64-8164-83ab-f05243e0d6ea",
            bot_new.notion_read_page,
        )
        if not body:
            return None
        return briefing_sources._render_onsr_tracker(body)
    except Exception as e:
        log.warning("onsr failed: %s", e)
        return f"(error: {e})"


def render_notion_todos_card():
    """Pull active to-dos via the briefing's existing renderer (which already
    filters out done/cancelled, sorts by priority, and formats nicely)."""
    try:
        import bot_new
        import briefing
        section = briefing.get_notion_todos_section(bot_new.notion_raw_query_database)
        return section or None
    except Exception as e:
        log.warning("notion_todos failed: %s", e)
        return f"(error: {e})"


def render_recent_notes_card(days=7, max_results=20):
    """Pull recent Apple Notes from iCloud via Mac bridge."""
    try:
        import bot_new
        result = bot_new.notes_recent(days=days, max_results=max_results)
        if not result:
            return None
        return result
    except Exception as e:
        log.warning("recent_notes failed: %s", e)
        return f"(error: {e})"


# --- HTML template --------------------------------------------------------

TEMPLATE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>Durgin Family Dashboard</title>
<style>
  :root {
    --bg: #f5f5f7;
    --card-bg: #ffffff;
    --text: #1d1d1f;
    --muted: #6e6e73;
    --accent: #0071e3;
    --border: #d2d2d7;
    --good: #1a8917;
    --warn: #c4690a;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1a1a1c;
      --card-bg: #2c2c2e;
      --text: #f5f5f7;
      --muted: #98989d;
      --accent: #2997ff;
      --border: #3a3a3c;
      --good: #30d158;
      --warn: #ff9f0a;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 16px;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
    font-size: 16px;
  }
  header {
    max-width: 1200px;
    margin: 0 auto 20px;
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 8px;
  }
  header h1 {
    margin: 0;
    font-size: 28px;
    font-weight: 700;
  }
  header .updated {
    color: var(--muted);
    font-size: 14px;
  }
  .grid {
    max-width: 1200px;
    margin: 0 auto;
    display: grid;
    grid-template-columns: 1fr;
    gap: 16px;
  }
  @media (min-width: 720px) {
    .grid { grid-template-columns: 1fr 1fr; }
    .card-wide { grid-column: 1 / -1; }
  }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 16px 20px;
    overflow: hidden;
  }
  .card h2 {
    margin: 0 0 12px;
    font-size: 18px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .card pre {
    margin: 0;
    white-space: pre-wrap;
    word-wrap: break-word;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    font-size: 14px;
    color: var(--text);
  }
  .refreshed {
    font-weight: normal;
    font-size: 12px;
    color: var(--muted);
    margin-left: 8px;
  }
  details.totals-toggle {
    margin-top: 12px;
    padding: 8px 12px;
    background: var(--bg);
    border-radius: 6px;
    border: 1px solid var(--border);
  }
  details.totals-toggle summary {
    cursor: pointer;
    color: var(--muted);
    font-size: 14px;
    user-select: none;
  }
  details.totals-toggle summary:hover {
    color: var(--text);
  }
  details.totals-toggle[open] summary {
    margin-bottom: 8px;
  }
  details.totals-toggle pre {
    margin: 0;
  }
  .card .empty {
    color: var(--muted);
    font-style: italic;
    font-size: 14px;
  }
  .card .subtle {
    color: var(--muted);
    font-size: 13px;
    margin-top: 8px;
    border-top: 1px solid var(--border);
    padding-top: 8px;
  }
  footer {
    max-width: 1200px;
    margin: 24px auto 8px;
    color: var(--muted);
    font-size: 12px;
    text-align: center;
  }
</style>
</head>
<body>
<header>
  <h1>🐾 Durgin Family</h1>
  <div class="updated">Updated {{ now }} · auto-refresh 5min</div>
</header>

<div class="grid">

  <div class="card card-wide">
    <h2>💰 Money <span class="refreshed">{% if money.refreshed %}refreshed {{ money.refreshed }}{% endif %}</span></h2>
    {% if money.balances_main %}
      <pre>{{ money.balances_main }}</pre>
    {% endif %}
    {% if money.upcoming %}
      <div class="subtle">{{ money.upcoming }}</div>
    {% endif %}
    {% if money.net_worth %}
      <div class="subtle">{{ money.net_worth }}</div>
    {% endif %}
    {% if money.totals %}
      <details class="totals-toggle">
        <summary>Show summary</summary>
        <pre>{{ money.totals }}</pre>
      </details>
    {% endif %}
    {% if not money.balances_main and not money.upcoming and not money.net_worth %}
      <div class="empty">No money data available</div>
    {% endif %}
  </div>

  <div class="card">
    <h2>🔨 House Projects <span class="refreshed">{% if refreshed %}refreshed {{ refreshed }}{% endif %}</span></h2>
    {% if house_projects %}
      <pre>{{ house_projects }}</pre>
    {% else %}
      <div class="empty">No items</div>
    {% endif %}
  </div>

  <div class="card">
    <h2>📊 ONSR Login Tracker <span class="refreshed">{% if refreshed %}refreshed {{ refreshed }}{% endif %}</span></h2>
    {% if onsr %}
      <pre>{{ onsr }}</pre>
    {% else %}
      <div class="empty">Tracker unavailable</div>
    {% endif %}
  </div>

  <div class="card card-wide">
    <h2>✅ Notion To-Dos <span class="refreshed">{% if refreshed %}refreshed {{ refreshed }}{% endif %}</span></h2>
    {% if notion_todos %}
      <pre>{{ notion_todos }}</pre>
    {% else %}
      <div class="empty">No active to-dos</div>
    {% endif %}
  </div>

  <div class="card card-wide">
    <h2>📝 Recent Notes (last 7 days) <span class="refreshed">{% if refreshed %}refreshed {{ refreshed }}{% endif %}</span></h2>
    {% if recent_notes %}
      <pre>{{ recent_notes }}</pre>
    {% else %}
      <div class="empty">No recent notes</div>
    {% endif %}
  </div>

</div>

<footer>Tailnet-only · clawdia-vps:8090 · No login required</footer>
</body>
</html>
""")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    now = datetime.now(EASTERN).strftime("%a %b %-d %-I:%M %p ET")
    refreshed = datetime.now(EASTERN).strftime("%-I:%M %p")
    money = render_money_card()
    house_projects = render_house_projects_card()
    onsr = render_onsr_card()
    notion_todos = render_notion_todos_card()
    recent_notes = render_recent_notes_card()
    html = TEMPLATE.render(
        now=now,
        money=money,
        refreshed=refreshed,
        house_projects=house_projects,
        onsr=onsr,
        notion_todos=notion_todos,
        recent_notes=recent_notes,
    )
    return HTMLResponse(content=html)


@app.get("/health", response_class=PlainTextResponse)
async def health():
    return "ok"
