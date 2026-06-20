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

# --- Portfolio source (Drive sheet + Alpha Vantage GLOBAL_QUOTE) ----------
# Free tier: 25 calls/day, 5/minute. With 4h cache TTL: each ticker refreshes
# up to 6x/day, comfortably supports up to 4 tickers (24 calls/day worst case).
# Bump TTL if Sean adds more positions, or upgrade Alpha Vantage tier.

_AV_CACHE = {}   # {ticker: (price_float, change_pct_float, as_of_str, fetched_at_float)}
_AV_CACHE_TTL = 14400  # 4 hours


def _alpha_vantage_quote(ticker, api_key):
    """Fetch GLOBAL_QUOTE for ticker. Returns (price, change_pct, as_of_str).
    Cached for 4h. Raises on failure or rate-limit (no 'Global Quote' in response)."""
    import time as _time
    now = _time.time()
    cached = _AV_CACHE.get(ticker)
    if cached and (now - cached[3]) < _AV_CACHE_TTL:
        return cached[0], cached[1], cached[2]
    import requests as _req
    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={ticker}&apikey={api_key}"
    r = _req.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    quote = data.get("Global Quote", {})
    if not quote or "05. price" not in quote:
        # Rate-limit/quota responses come as {"Note": "..."} or {"Information": "..."}.
        note = data.get("Note") or data.get("Information") or str(data)[:200]
        raise RuntimeError(f"Alpha Vantage no quote for {ticker}: {note[:200]}")
    price = float(quote["05. price"])
    change_pct = float(quote["10. change percent"].rstrip("%"))
    as_of = quote["07. latest trading day"]
    _AV_CACHE[ticker] = (price, change_pct, as_of, now)
    return price, change_pct, as_of


def _fetch_portfolio_data(sheet_id, av_api_key):
    """Read Portfolio.xlsx from family Drive, fetch live prices, compute P&L."""
    import json as _json, io as _io, csv as _csv
    from google.oauth2.credentials import Credentials as _Cred
    from googleapiclient.discovery import build as _build
    with open("/etc/clawdia/google_token_family.json") as f:
        creds = _Cred.from_authorized_user_info(_json.load(f))
    drive = _build("drive", "v3", credentials=creds, cache_discovery=False)
    csv_bytes = drive.files().export(fileId=sheet_id, mimeType="text/csv").execute()
    reader = _csv.DictReader(_io.StringIO(csv_bytes.decode("utf-8")))
    rows = list(reader)

    positions = []
    errors = []
    for row in rows:
        ticker = (row.get("Ticker") or "").strip().upper()
        if not ticker:
            continue
        try:
            shares = float(row.get("Shares", 0))
            total_cost_basis = float(row.get("Total Cost Basis", 0))
            cost_basis_per_share = float(row.get("Cost Basis / Share", 0))
        except (ValueError, TypeError):
            errors.append((ticker, "non-numeric value in sheet"))
            continue
        try:
            price, change_pct, as_of = _alpha_vantage_quote(ticker, av_api_key)
        except Exception as e:
            errors.append((ticker, f"{type(e).__name__}: {str(e)[:80]}"))
            continue
        current_value = round(shares * price, 2)
        gain_loss = round(current_value - total_cost_basis, 2)
        gain_loss_pct = round((gain_loss / total_cost_basis) * 100, 2) if total_cost_basis else 0.0
        positions.append({
            "ticker": ticker,
            "company": row.get("Company", ""),
            "shares": shares,
            "cost_basis_per_share": cost_basis_per_share,
            "total_cost_basis": total_cost_basis,
            "current_price": price,
            "current_value": current_value,
            "gain_loss_dollars": gain_loss,
            "gain_loss_percent": gain_loss_pct,
            "change_today_percent": change_pct,
            "as_of": as_of,
        })
    return {
        "positions": positions,
        "errors": errors,
        "total_cost_basis": round(sum(p["total_cost_basis"] for p in positions), 2),
        "total_current_value": round(sum(p["current_value"] for p in positions), 2),
    }


def _render_portfolio_section(data):
    """Format portfolio data as markdown for the morning brief."""
    if not data["positions"] and not data["errors"]:
        return ""
    lines = []
    for p in data["positions"]:
        arrow = "📈" if p["gain_loss_dollars"] >= 0 else "📉"
        sign = "+" if p["gain_loss_dollars"] >= 0 else ""
        today_sign = "+" if p["change_today_percent"] >= 0 else ""
        shares_str = str(int(p["shares"])) if p["shares"] == int(p["shares"]) else f"{p['shares']:.4f}".rstrip("0").rstrip(".")
        lines.append(
            f"{arrow} *{p['ticker']}* ({p['company']}): {shares_str} sh @ ${p['current_price']:.2f} = ${p['current_value']:,.2f}"
        )
        lines.append(
            f"   P&L: {sign}${p['gain_loss_dollars']:,.2f} ({sign}{p['gain_loss_percent']:.2f}%) · Today: {today_sign}{p['change_today_percent']:.2f}% · as of {p['as_of']}"
        )
    if len(data["positions"]) > 1 and data["total_cost_basis"] > 0:
        total_pl = round(data["total_current_value"] - data["total_cost_basis"], 2)
        total_pct = round((total_pl / data["total_cost_basis"]) * 100, 2)
        sign = "+" if total_pl >= 0 else ""
        lines.append("")
        lines.append(f"*Portfolio Total*: ${data['total_current_value']:,.2f} · {sign}${total_pl:,.2f} ({sign}{total_pct:.2f}%)")
    for ticker, err in data["errors"]:
        lines.append(f"   ⚠️ {ticker}: {err}")
    return chr(10).join(lines)


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
    {
        "key": "portfolio",
        "header": "💰 *Portfolio*",
        "type": "portfolio",
        "source": "13sGX8Q_8d0DkOt6eHlI-YPdL9vEByfQ7EnDogEbUkVk",
        "renderer": _render_portfolio_section,
    },
]


# --- Public entry point ----------------------------------------------------

def render_watched_sources(notion_read_fn=None, return_health=False):
    """Iterate WATCHED_SOURCES, fetch each, render to a section. Return list of
    formatted section strings (each already includes its header). Sources that
    fail or return empty are silently omitted from the output.

    If return_health=True, returns (sections, health_report) where health_report
    is dict {"ok": [keys], "empty": [keys], "failed": [(key, error_str)],
    "unknown_type": [keys]}. Caller can use this to emit a Sysmon heartbeat
    when any source fails, eliminating the silent-degradation failure mode
    that previously hid Tailscale outages, Notion rate-limits, etc.
    """
    sections = []
    health = {"ok": [], "empty": [], "failed": [], "unknown_type": []}
    for src in WATCHED_SOURCES:
        key = src.get("key", "?")
        try:
            if src["type"] == "apple_note":
                body = _fetch_apple_note(src["source"])
            elif src["type"] == "notion_page":
                body = _fetch_notion_page_text(src["source"], notion_read_fn)
            elif src["type"] == "portfolio":
                import os as _os
                av_key = _os.environ.get("ALPHA_VANTAGE_API_KEY", "")
                if not av_key:
                    raise RuntimeError("ALPHA_VANTAGE_API_KEY env var not set")
                body = _fetch_portfolio_data(src["source"], av_key)
            else:
                log.warning("unknown source type %r", src["type"])
                health["unknown_type"].append(key)
                continue
            rendered = src["renderer"](body)
            if rendered:
                sections.append(f"{src['header']}\n{rendered}")
                health["ok"].append(key)
            else:
                log.info("source %s returned empty; skipping", key)
                health["empty"].append(key)
        except Exception as e:
            log.warning("source %s failed: %s", key, e)
            health["failed"].append((key, f"{type(e).__name__}: {str(e)[:200]}"))
            continue
    if return_health:
        return sections, health
    return sections
