"""Apify Facebook Marketplace integration for Clawdia.

Two flavors:
1. One-shot search via marketplace_search() — Sean asks Clawdia, results returned.
2. Saved monitors via marketplace_monitor() — persistent, scheduled, deduped, alerts on new matches.

Uses apify/facebook-marketplace-scraper (~$0.005/listing). Spend protection via daily call cap.
"""
import os, json, sqlite3, logging, urllib.parse, asyncio, threading, time
from datetime import datetime, timezone, timedelta
import requests

log = logging.getLogger("clawdia.marketplace")

DB_PATH = "/var/lib/clawdia/memory.db"
ACTOR_ID = "apify~facebook-marketplace-scraper"
APIFY_API_BASE = "https://api.apify.com/v2"

# Location IDs from Facebook Marketplace URLs
LOCATIONS = {
    "north_east_md": {"name": "North East, MD", "id": "103803479657940"},
    "sterling_va":   {"name": "Sterling, VA",   "id": "103978396303904"},
}

# Spend protection: hard cap on Actor calls per UTC day. Each call costs ~$0.005-$0.50.
# At 30 calls/day max, worst case ~$15/mo if every call returns 50 listings (which is rare).
# Typical: 30 calls × ~10 results avg = ~$1.50/mo. Well under $5 free tier.
DAILY_CALL_CAP = 30
PER_CALL_RESULTS_HARD_CAP = 50

# ──────────────────────── DB schema ────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS marketplace_monitors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        keyword TEXT NOT NULL,
        location TEXT NOT NULL DEFAULT 'both',
        min_price INTEGER,
        max_price INTEGER,
        max_results INTEGER NOT NULL DEFAULT 25,
        active INTEGER NOT NULL DEFAULT 1,
        last_run TEXT,
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS marketplace_seen_listings (
        monitor_id INTEGER NOT NULL,
        listing_id TEXT NOT NULL,
        first_seen TEXT NOT NULL,
        PRIMARY KEY (monitor_id, listing_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS apify_call_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        called_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        results_count INTEGER,
        purpose TEXT
    )""")
    c.commit()
    return c


# ──────────────────────── URL building ────────────────────────

def _build_search_url(keyword, location_key, min_price=None, max_price=None):
    """Build a Facebook Marketplace search URL for one location."""
    loc = LOCATIONS.get(location_key)
    if not loc:
        raise ValueError(f"Unknown location: {location_key}")
    params = {"query": keyword}
    if min_price is not None:
        params["minPrice"] = int(min_price)
    if max_price is not None:
        params["maxPrice"] = int(max_price)
    qs = urllib.parse.urlencode(params)
    return f"https://www.facebook.com/marketplace/{loc['id']}/search?{qs}"


def _resolve_locations(location_field):
    """Convert a monitor's location field into a list of location keys to query.
    'both' -> [north_east_md, sterling_va]. Otherwise the named single key."""
    if not location_field or location_field == "both":
        return ["north_east_md", "sterling_va"]
    if location_field in LOCATIONS:
        return [location_field]
    raise ValueError(f"Invalid location: {location_field}")


# ──────────────────────── Spend protection ────────────────────────

def _today_call_count():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) FROM apify_call_log WHERE substr(called_at,1,10)=?",
            (today,)
        ).fetchone()
    return row[0] if row else 0


def _log_call(actor, results_count, purpose):
    with _conn() as c:
        c.execute(
            "INSERT INTO apify_call_log (called_at, actor, results_count, purpose) VALUES (?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), actor, results_count, purpose)
        )


# ──────────────────────── Actor execution ────────────────────────

_LIMITS_CACHE = {"data": None, "fetched_at": 0.0}
_LIMITS_CACHE_TTL = 600  # 10 minutes; bust on platform-feature-disabled

def _fetch_apify_limits(force=False):
    """Fetch and cache /users/me/limits. Returns dict or None on failure."""
    import time as _t, requests as _r
    now = _t.time()
    if not force and _LIMITS_CACHE["data"] is not None:
        if now - _LIMITS_CACHE["fetched_at"] < _LIMITS_CACHE_TTL:
            return _LIMITS_CACHE["data"]
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        return None
    try:
        resp = _r.get(f"{APIFY_API_BASE}/users/me/limits",
                      params={"token": token}, timeout=10)
        if resp.status_code == 200:
            d = resp.json().get("data", {})
            _LIMITS_CACHE["data"] = d
            _LIMITS_CACHE["fetched_at"] = now
            return d
        log.warning("Apify limits fetch returned %s", resp.status_code)
    except Exception as e:
        log.warning("Apify limits fetch failed: %s", e)
    return _LIMITS_CACHE.get("data")  # stale data better than nothing


def _fmt_et(utc_iso):
    """Format a UTC ISO timestamp string as ET weekday + 12-hour clock + tz abbrev.
    e.g. '2026-06-23T23:59:59.999Z' -> 'Tue Jun 23, 7:59 PM EDT'.
    Returns the input string unchanged on any failure (defensive)."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
        et = dt.astimezone(ZoneInfo("America/New_York"))
        return et.strftime("%a %b %-d, %-I:%M %p %Z")
    except Exception:
        return utc_iso


def _apify_is_available():
    """Return (available: bool, reason: str). Available iff under monthly cap
    AND we are within the current usage cycle. The cycle-end check auto-recovers
    after a billing rollover even if our cache is stale."""
    from datetime import datetime, timezone
    limits = _fetch_apify_limits()
    if limits is None:
        # No data; allow attempt -- a real 403 will trip the breaker below.
        return True, "limits-unknown (no fetch); allowing attempt"
    current = float(limits.get("current", {}).get("monthlyUsageUsd", 0) or 0)
    cap = float(limits.get("limits", {}).get("maxMonthlyUsageUsd", 5) or 5)
    cycle = limits.get("monthlyUsageCycle", {}) or {}
    end_str = cycle.get("endAt", "")
    # If cycle ended already (per cached data), allow -- this self-heals after rollover.
    try:
        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > end_dt:
            return True, f"cycle ended {end_str}; allowing attempt to refresh cache"
    except Exception:
        pass
    if current >= cap:
        return False, (f"Monthly usage hard limit hit: ${current:.2f} / ${cap:.2f}. "
                       f"Cycle resets {_fmt_et(end_str)}.")
    return True, f"OK: ${current:.2f} / ${cap:.2f} used; cycle ends {_fmt_et(end_str)}."


def apify_status():
    """Public: human-readable Apify account status. Authoritative for
    'is apify working' questions. Called via the apify_status tool."""
    from datetime import datetime, timezone
    limits = _fetch_apify_limits(force=True)  # always fresh on explicit ask
    today_calls = _today_call_count()
    if limits is None:
        return (f"Apify status: limits API unreachable.\n"
                f"Daily call counter: {today_calls}/{DAILY_CALL_CAP} (resets at next UTC midnight).")
    current = float(limits.get("current", {}).get("monthlyUsageUsd", 0) or 0)
    cap = float(limits.get("limits", {}).get("maxMonthlyUsageUsd", 5) or 5)
    cycle = limits.get("monthlyUsageCycle", {}) or {}
    start_str = cycle.get("startAt", "?")
    end_str = cycle.get("endAt", "?")
    pct = (current / cap * 100) if cap > 0 else 0
    available, reason = _apify_is_available()
    lines = [
        f"Apify account status (live):",
        f"  Current cycle: {start_str} -> {end_str} (UTC)",
        f"  Cycle resets: {_fmt_et(end_str)}  <- USE THIS for 'when will apify be back' answers",
        f"  Usage: ${current:.4f} / ${cap:.2f} USD ({pct:.1f}%)",
        f"  Status: {'AVAILABLE' if available else 'BLOCKED until cycle reset'}",
        f"  Daily call counter: {today_calls}/{DAILY_CALL_CAP} (resets at next UTC midnight)",
        f"  Reason: {reason}",
    ]
    return "\n".join(lines)


def _run_actor_sync(start_url, results_limit, purpose="search"):
    """Run the FB marketplace scraper synchronously and return parsed listings.
    Uses run-sync-get-dataset-items endpoint with a 90-second timeout."""
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        raise RuntimeError("APIFY_API_TOKEN not set in /etc/clawdia/env")

    # Patch F: circuit breaker -- refuse if monthly cap exceeded
    available, reason = _apify_is_available()
    if not available:
        raise RuntimeError(f"Apify circuit breaker: {reason}")

    if _today_call_count() >= DAILY_CALL_CAP:
        raise RuntimeError(
            f"Daily Apify call cap of {DAILY_CALL_CAP} hit. "
            f"Resets at UTC midnight. Current usage protects against runaway spend."
        )

    capped_limit = min(results_limit, PER_CALL_RESULTS_HARD_CAP)
    payload = {
        "startUrls": [{"url": start_url}],
        "resultsLimit": capped_limit,
        "includeListingDetails": False,  # cheaper; price + title + URL is what we need
    }

    url = f"{APIFY_API_BASE}/acts/{ACTOR_ID}/run-sync-get-dataset-items?token={token}&timeout=90"
    log.info("Apify run-sync: actor=%s purpose=%s limit=%d", ACTOR_ID, purpose, capped_limit)

    r = requests.post(url, json=payload, timeout=120)
    # Log the call regardless of success — caps are based on attempts, not successes
    if r.status_code in (200, 201):
        try:
            data = r.json()
        except Exception:
            data = []
        if not isinstance(data, list):
            data = []
        _log_call(ACTOR_ID, len(data), purpose)
        return data
    else:
        _log_call(ACTOR_ID, 0, purpose + ":failed")
        log.warning("Apify call failed: status=%s body=%s", r.status_code, r.text[:300])
        # If Apify says monthly limit, bust limits cache so next status check is fresh
        if r.status_code == 403 and "platform-feature-disabled" in r.text:
            _LIMITS_CACHE["fetched_at"] = 0.0
            log.warning("Apify limits cache busted (platform-feature-disabled)")
        raise RuntimeError(f"Apify error {r.status_code}: {r.text[:200]}")


# ──────────────────────── Result formatting ────────────────────────

def _format_listing(item):
    """Extract canonical fields from an Apify listing record. Real schema as of
    apify/facebook-marketplace-scraper Apr 2026: marketplace_listing_title,
    listing_price (dict with formatted_amount), listingUrl, id."""
    # Price: dict like {"formatted_amount": "$44.09", "amount": "44.09"}
    raw_price = item.get("listing_price") or item.get("price") or {}
    if isinstance(raw_price, dict):
        price_str = raw_price.get("formatted_amount") or (
            f"${raw_price.get('amount')}" if raw_price.get('amount') else None)
    else:
        price_str = str(raw_price) if raw_price else None
    # Location: dict like {"reverse_geocode": {...}} — usually unhelpful, keep blank
    raw_loc = item.get("location") or {}
    loc_str = ""
    if isinstance(raw_loc, dict):
        rg = raw_loc.get("reverse_geocode")
        if isinstance(rg, dict):
            loc_str = rg.get("display") or rg.get("city") or ""
    elif isinstance(raw_loc, str):
        loc_str = raw_loc
    # Status flags — skip sold/hidden/pending listings entirely
    if item.get("is_sold") or item.get("is_hidden"):
        return None
    return {
        "id": str(item.get("id") or item.get("listingId") or ""),
        "title": item.get("marketplace_listing_title") or item.get("custom_title") or item.get("title") or "(untitled)",
        "price": price_str,
        "url": item.get("listingUrl") or item.get("listing_url") or item.get("url", ""),
        "location": loc_str,
    }


def _format_listings_text(listings, header_line=None):
    """Pretty-print a list of formatted listing dicts for Telegram."""
    if not listings:
        return (header_line + "\n" if header_line else "") + "No matching listings found."
    lines = []
    if header_line:
        lines.append(header_line)
    for L in listings[:50]:
        title = (L.get("title") or "(untitled)")[:60]
        price = L.get("price") or "—"
        loc = L.get("location") or L.get("_loc") or ""
        url = L.get("url") or ""
        lines.append(f"• {title} — {price}{(' [' + str(loc) + ']') if loc else ''}\n  {url}")
    return "\n".join(lines)


# ──────────────────────── One-shot search ────────────────────────

def marketplace_search(keyword, location="both", min_price=None, max_price=None, max_results=25):
    """One-shot search across one or both locations. Returns formatted text."""
    if not keyword or not isinstance(keyword, str):
        return "ERROR: keyword (non-empty string) is required."

    try:
        loc_keys = _resolve_locations(location)
    except ValueError as e:
        return f"ERROR: {e}. Valid: 'both', 'north_east_md', 'sterling_va'."

    per_loc = max(1, min(int(max_results) // len(loc_keys), PER_CALL_RESULTS_HARD_CAP))

    all_results = []
    errors = []
    for loc_key in loc_keys:
        try:
            url = _build_search_url(keyword, loc_key, min_price, max_price)
            raw = _run_actor_sync(url, per_loc, purpose=f"search:{loc_key}")
            for item in raw:
                fmt = _format_listing(item)
                if fmt is None:
                    continue  # sold/hidden listing — skip
                fmt["_loc"] = LOCATIONS[loc_key]["name"]
                all_results.append(fmt)
        except Exception as e:
            errors.append(f"{LOCATIONS[loc_key]['name']}: {e}")
            log.warning("search failed for %s: %s", loc_key, e)

    header = f"Marketplace search: '{keyword}'"
    if min_price is not None or max_price is not None:
        rng = []
        if min_price is not None: rng.append(f"min ${min_price}")
        if max_price is not None: rng.append(f"max ${max_price}")
        header += f" ({', '.join(rng)})"
    header += f" — {len(all_results)} result(s) across {len(loc_keys)} location(s)"

    out = _format_listings_text(all_results, header)
    if errors:
        out += "\n\n⚠️ Partial errors:\n" + "\n".join("  " + e for e in errors)
    return out


# ──────────────────────── Monitor CRUD ────────────────────────

def monitor_add(name, keyword, location="both", min_price=None, max_price=None, max_results=25):
    if not name or not keyword:
        return "ERROR: monitor_add requires name and keyword."
    try:
        _resolve_locations(location)
    except ValueError as e:
        return f"ERROR: {e}"
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO marketplace_monitors (name, keyword, location, min_price, max_price, max_results, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (name, keyword, location, min_price, max_price, int(max_results),
                 datetime.now(timezone.utc).isoformat())
            )
        return f"Monitor '{name}' added: keyword='{keyword}' location={location} price=${min_price or 0}-${max_price or '∞'} max_results={max_results}. Will run hourly."
    except sqlite3.IntegrityError:
        return f"ERROR: monitor named '{name}' already exists. Delete it first or pick a different name."


def monitor_list():
    with _conn() as c:
        rows = c.execute(
            "SELECT id, name, keyword, location, min_price, max_price, max_results, active, last_run "
            "FROM marketplace_monitors ORDER BY id"
        ).fetchall()
    if not rows:
        return "No monitors configured. Use marketplace_monitor with action=add to create one."
    lines = [f"Monitors ({len(rows)}):"]
    for r in rows:
        mid, name, kw, loc, mn, mx, lim, act, last = r
        status = "✓" if act else "✗"
        price_s = ""
        if mn or mx:
            price_s = f" ${mn or 0}-${mx or '∞'}"
        lines.append(f"  [{mid}] {status} {name}: '{kw}' @ {loc}{price_s}, limit {lim}, last run {last or 'never'}")
    return "\n".join(lines)


def monitor_pause(name_or_id):
    """Mark a monitor inactive (active=0). Scheduler skips inactive monitors."""
    with _conn() as c:
        if str(name_or_id).isdigit():
            cur = c.execute("UPDATE marketplace_monitors SET active=0 WHERE id=?", (int(name_or_id),))
        else:
            cur = c.execute("UPDATE marketplace_monitors SET active=0 WHERE name=?", (name_or_id,))
        if cur.rowcount == 0:
            return f"No monitor matched {name_or_id!r}"
        c.commit()
        return f"Paused {cur.rowcount} monitor(s) matching {name_or_id!r}. Resume with action='resume'."


def monitor_resume(name_or_id):
    """Mark a monitor active (active=1). Scheduler resumes on next tick (hourly)."""
    with _conn() as c:
        if str(name_or_id).isdigit():
            cur = c.execute("UPDATE marketplace_monitors SET active=1 WHERE id=?", (int(name_or_id),))
        else:
            cur = c.execute("UPDATE marketplace_monitors SET active=1 WHERE name=?", (name_or_id,))
        if cur.rowcount == 0:
            return f"No monitor matched {name_or_id!r}"
        c.commit()
        return f"Resumed {cur.rowcount} monitor(s) matching {name_or_id!r}. Next run within the hour."


def monitor_delete(name_or_id):
    """Accept either name string or numeric id."""
    with _conn() as c:
        if str(name_or_id).isdigit():
            cur = c.execute("DELETE FROM marketplace_monitors WHERE id=?", (int(name_or_id),))
        else:
            cur = c.execute("DELETE FROM marketplace_monitors WHERE name=?", (name_or_id,))
        n = cur.rowcount
        # Cascade dedup table cleanup
        if n:
            c.execute("DELETE FROM marketplace_seen_listings WHERE monitor_id NOT IN (SELECT id FROM marketplace_monitors)")
    return f"Deleted {n} monitor(s)." if n else f"No monitor matched '{name_or_id}'."


def monitor_run_now(name_or_id):
    """Force-run a monitor right now (one-shot, like search but persists dedup state)."""
    with _conn() as c:
        if str(name_or_id).isdigit():
            row = c.execute("SELECT * FROM marketplace_monitors WHERE id=?", (int(name_or_id),)).fetchone()
        else:
            row = c.execute("SELECT * FROM marketplace_monitors WHERE name=?", (name_or_id,)).fetchone()
    if not row:
        return f"No monitor matched '{name_or_id}'."
    return _run_one_monitor_sync(row, force=True)


def _run_one_monitor_sync(row, force=False):
    """Run a monitor row, dedupe against seen listings, return formatted output of NEW matches."""
    mid, name, keyword, location, mn, mx, lim, active, last_run, created = row
    if not active and not force:
        return f"Monitor '{name}' is paused, skipping."

    try:
        loc_keys = _resolve_locations(location)
    except ValueError as e:
        return f"ERROR: {e}"

    per_loc = max(1, min(int(lim) // len(loc_keys), PER_CALL_RESULTS_HARD_CAP))
    new_listings = []
    errors = []
    for loc_key in loc_keys:
        try:
            url = _build_search_url(keyword, loc_key, mn, mx)
            raw = _run_actor_sync(url, per_loc, purpose=f"monitor:{name}:{loc_key}")
            for item in raw:
                fmt = _format_listing(item)
                if fmt is None:
                    continue
                fmt["_loc"] = LOCATIONS[loc_key]["name"]
                new_listings.append(fmt)
        except Exception as e:
            errors.append(f"{LOCATIONS[loc_key]['name']}: {e}")
            log.warning("monitor %s failed for %s: %s", name, loc_key, e)

    # Dedup
    truly_new = []
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        for L in new_listings:
            lid = L["id"]
            if not lid: continue
            cur = c.execute(
                "SELECT 1 FROM marketplace_seen_listings WHERE monitor_id=? AND listing_id=?",
                (mid, lid)
            ).fetchone()
            if cur is None:
                truly_new.append(L)
                c.execute(
                    "INSERT OR IGNORE INTO marketplace_seen_listings (monitor_id, listing_id, first_seen) VALUES (?,?,?)",
                    (mid, lid, now)
                )
        c.execute("UPDATE marketplace_monitors SET last_run=? WHERE id=?", (now, mid))

    header = f"Monitor '{name}' — {len(truly_new)} NEW match(es) (out of {len(new_listings)} returned)"
    out = _format_listings_text(truly_new, header)
    if errors:
        out += "\n\n⚠️ Partial errors:\n" + "\n".join("  " + e for e in errors)
    return out


# ──────────────────────── Tool entry point (multi-action) ────────────────────────

def marketplace_monitor(action, name=None, keyword=None, location="both",
                        min_price=None, max_price=None, max_results=25):
    """Single tool wrapping the CRUD verbs so Clawdia can manage monitors via one schema."""
    a = (action or "").lower().strip()
    if a == "list":
        return monitor_list()
    if a == "add":
        if not name: return "ERROR: 'name' required for add."
        if not keyword: return "ERROR: 'keyword' required for add."
        return monitor_add(name, keyword, location, min_price, max_price, max_results)
    if a == "delete":
        if not name: return "ERROR: 'name' required for delete (can be name or numeric id)."
        return monitor_delete(name)
    if a == "run_now":
        if not name: return "ERROR: 'name' required for run_now."
        return monitor_run_now(name)
    if a == "pause":
        if not name: return "ERROR: 'name' required for pause (can be name or numeric id)."
        return monitor_pause(name)
    if a == "resume":
        if not name: return "ERROR: 'name' required for resume (can be name or numeric id)."
        return monitor_resume(name)
    return f"ERROR: unknown action '{action}'. Valid: list, add, pause, resume, delete, run_now."


# ──────────────────────── Scheduler ────────────────────────

def _is_quiet_hours_et():
    """Same convention as calendar nudges: 10pm–7am ET."""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
        h = now_et.hour
        return h >= 22 or h < 7
    except Exception:
        return False


def start_marketplace_monitor_scheduler(app, owner_id, interval_sec=3600):
    """Background thread that runs every `interval_sec` (default 60min). For each
    active monitor: run, dedupe, send Telegram alert ONLY if new matches found.
    Skips during quiet hours (10pm–7am ET)."""
    log = logging.getLogger("clawdia.marketplace.scheduler")

    async def send_alert(text):
        try:
            # Telegram message length cap is 4096; truncate and indicate truncation
            if len(text) > 3900:
                text = text[:3900] + "\n\n…(truncated)"
            await app.bot.send_message(chat_id=owner_id, text=text)
        except Exception as e:
            log.error("alert send failed: %s", e)

    def loop():
        time.sleep(60)  # let bot finish booting
        log.info("Marketplace monitor scheduler running (interval=%ds)", interval_sec)
        while True:
            try:
                if _is_quiet_hours_et():
                    log.debug("quiet hours, skipping")
                else:
                    with _conn() as c:
                        rows = c.execute(
                            "SELECT id, name, keyword, location, min_price, max_price, max_results, "
                            "active, last_run, created_at FROM marketplace_monitors WHERE active=1"
                        ).fetchall()
                    for row in rows:
                        try:
                            result = _run_one_monitor_sync(row)
                            # Only alert if we have at least one truly new listing
                            if result and "NEW match" in result and "0 NEW match" not in result:
                                fut = asyncio.run_coroutine_threadsafe(send_alert(result), app.loop) \
                                      if hasattr(app, 'loop') else None
                                if fut is None:
                                    # Newer python-telegram-bot uses a different event loop ref
                                    asyncio.run(send_alert(result))
                        except Exception as e:
                            log.error("monitor run failed for row %s: %s", row[1] if len(row) > 1 else "?", e)
            except Exception as e:
                log.exception("scheduler loop error: %s", e)
            time.sleep(interval_sec)

    threading.Thread(target=loop, daemon=True, name="marketplace_monitor_scheduler").start()


# ──────────────────────── Google Flights via johnvc/Google-Flights-Data-Scraper ────────────────────────

AIRFARE_ACTOR = "johnvc~Google-Flights-Data-Scraper-Flight-and-Price-Search"

_LOYALTY_PROGRAMS = {
    "southwest": ("Southwest Rapid Rewards #154113886 (58,285 pts as of May 2026)", ["southwest", "wn"]),
    "united":    ("United MileagePlus #VF495055 (military discount available)",     ["united", "ua"]),
    "american":  ("American AAdvantage #35BHJ48",                                    ["american airlines", "american", "aa"]),
}


def _airfare_loyalty_match(airlines_in_results):
    if not airlines_in_results:
        return []
    obs_lower = " ".join(str(a).lower() for a in airlines_in_results)
    return [note for key, (note, aliases) in _LOYALTY_PROGRAMS.items()
            if any(a in obs_lower for a in aliases)]


def airfare_search(departure, arrival, depart_date, return_date=None,
                  passengers=1, max_results=10, exclude_basic=False):
    """Search Google Flights via Apify. Returns formatted text result."""
    token = os.environ.get("APIFY_API_TOKEN", "")
    if not token:
        return "airfare_search error: APIFY_API_TOKEN not set."

    payload = {
        "departure_id": departure,
        "arrival_id": arrival,
        "outbound_date": depart_date,
        "exclude_basic": bool(exclude_basic),
        "fetch_booking_options": False,
    }
    if return_date:
        payload["return_date"] = return_date
    if passengers and int(passengers) > 1:
        payload["adults"] = int(passengers)

    try:
        run_resp = requests.post(
            f"{APIFY_API_BASE}/acts/{AIRFARE_ACTOR}/runs",
            params={"token": token, "waitForFinish": 90},
            json=payload,
            timeout=120,
        )
    except requests.exceptions.Timeout:
        return "airfare_search error: actor run timed out after 120s."
    except Exception as e:
        return f"airfare_search error starting run: {type(e).__name__}: {e}"

    if run_resp.status_code not in (200, 201):
        return f"airfare_search error: HTTP {run_resp.status_code} from Apify. Body: {run_resp.text[:300]}"
    run_data = run_resp.json().get("data", {})
    run_status = run_data.get("status")
    dataset_id = run_data.get("defaultDatasetId")
    if not dataset_id:
        return f"airfare_search error: no dataset returned. status={run_status}"
    if run_status not in ("SUCCEEDED", "RUNNING"):
        return f"airfare_search error: run did not succeed (status={run_status}). Check Apify console."

    try:
        items_resp = requests.get(
            f"{APIFY_API_BASE}/datasets/{dataset_id}/items",
            params={"token": token, "format": "json", "limit": max_results},
            timeout=30,
        )
    except Exception as e:
        return f"airfare_search error fetching dataset: {type(e).__name__}: {e}"
    if items_resp.status_code != 200:
        return f"airfare_search error: HTTP {items_resp.status_code} fetching dataset."

    try:
        items = items_resp.json()
    except Exception:
        return "airfare_search error: dataset response not valid JSON."
    if not items:
        ret = f" return {return_date}" if return_date else ""
        return f"airfare_search: no flights found for {departure} -> {arrival} on {depart_date}{ret}."

    # The actor returns ONE item containing best_flights[] + other_flights[].
    # Each entry has flights[] (segments), price, total_duration, etc.
    top = items[0] if items else {}
    best = top.get("best_flights", []) or []
    other = top.get("other_flights", []) or []
    all_options = best + other
    total_found = top.get("search_metadata", {}).get("total_flights_found", len(all_options))

    ret = f" return {return_date}" if return_date else ""
    lines = [f"Flights: {departure} -> {arrival} -- {depart_date}{ret} ({passengers} pax)"]
    lines.append(f"   {total_found} total flight(s) found; showing top {min(len(all_options), max_results)}:")
    airlines_seen = []

    def _fmt_duration(mins):
        try:
            mins = int(mins)
            h, m = divmod(mins, 60)
            return f"{h}h{m:02d}m"
        except Exception:
            return "?"

    for i, opt in enumerate(all_options[:max_results], 1):
        price = opt.get("price")
        price_str = f"${price}" if isinstance(price, (int, float)) else "?"
        segments = opt.get("flights", []) or []
        if not segments:
            continue
        # Airline from first segment; if multi-carrier, list all
        airlines = list(dict.fromkeys(str(s.get("airline", "")).strip() for s in segments if s.get("airline")))
        airline_str = " / ".join(airlines) if airlines else "?"
        airlines_seen.extend(airlines)
        stops = max(0, len(segments) - 1)
        stops_str = "nonstop" if stops == 0 else f"{stops} stop"
        duration = _fmt_duration(opt.get("total_duration"))
        # Times from first/last segment
        dep_time = segments[0].get("departure_airport", {}).get("time", "")
        arr_time = segments[-1].get("arrival_airport", {}).get("time", "")
        # Trim "YYYY-MM-DD " prefix from times if both have same date
        def _shorten(t):
            return t.split(" ")[-1] if " " in t else t
        time_str = f"{_shorten(dep_time)} -> {_shorten(arr_time)}"
        flag = " [best]" if opt in best else ""
        line = f"  {i}. {price_str:>7}  {airline_str:18} {stops_str:8} {duration:>7}  ({time_str}){flag}"
        lines.append(line)

    hits = _airfare_loyalty_match(airlines_seen)
    if hits:
        lines.append("")
        lines.append("Loyalty programs to use:")
        for h in hits:
            lines.append(f"   - {h}")
    else:
        lines.append("")
        lines.append("None of the saved loyalty programs match these airlines.")

    lines.append("")
    lines.append("Retired military: United/Delta/American offer discounts via ID.me or GovX -- worth checking the carrier app before paying retail.")

    return "\n".join(lines)
