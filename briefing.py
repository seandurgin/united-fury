#!/usr/bin/env python3
"""Morning briefing module for Clawdia."""
import asyncio, logging, threading, time, subprocess
from datetime import datetime, timedelta
import zoneinfo
import httpx

log = logging.getLogger("clawdia.briefing")


def _notion_read_for_briefing(page_id):
    """Lazy wrapper: import bot_new on first call to avoid circular import."""
    try:
        import bot_new
        return bot_new.notion_read_page(page_id)
    except Exception as e:
        log.warning("_notion_read_for_briefing failed: %s", e)
        return None
EASTERN = zoneinfo.ZoneInfo("America/New_York")


async def get_weather():
    """Call the new get_weather tool from bot_new.py (Open-Meteo, real
    forecast formatted for humans). Falls back to wttr.in shorthand if
    the import fails for some reason."""
    try:
        import importlib.util, asyncio
        spec = importlib.util.spec_from_file_location("bot_new", "/opt/clawdia/bot_new.py")
        bn = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bn)
        # The real tool is sync; run in thread to keep async signature
        result = await asyncio.to_thread(bn.get_weather, "home", 3)
        # The tool returns a multi-line string starting "Weather for North East, MD:"
        # Strip the redundant first line since briefing already has a "Weather" header.
        lines = result.split("\n")
        if lines and lines[0].lower().startswith("weather for"):
            return "\n".join(lines[1:]).strip()
        return result.strip()
    except Exception as e:
        # Fallback: short wttr.in (preserves old behavior if new tool breaks)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://wttr.in/North+East,MD",
                    params={"format": "3"},
                    headers={"User-Agent": "curl/7.68.0"}
                )
                return r.text.strip()
        except Exception as e2:
            return f"Weather unavailable: {e}"

def get_todo_tasks(get_conn):
    """Get scheduled tasks due today or upcoming."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, schedule, prompt, next_run FROM scheduled_tasks WHERE active=1 ORDER BY next_run LIMIT 10"
            ).fetchall()
        if not rows:
            return "No scheduled tasks."
        lines = []
        for row in rows:
            next_run = row[3][:16] if row[3] else "?"
            lines.append(f"• [{row[0]}] {row[2]} — next: {next_run}")
        return "\n".join(lines)
    except Exception as e:
        return f"Tasks unavailable: {e}"

def _humanize_calendar(cal_text):
    """Strip ID strings and prettify dates from calendar_get_upcoming output.
    Input lines look like:
      - 2026-05-05: Hailey Durgin's birthday (ID: q25iad0llchg52pk6tamfftf2k_20260505)
      - 2026-05-15T09:00:00-04:00: TurboTax Payment -- $242.76 (ID: i3itt5fe...)
    Output: prettier human-readable lines.
    """
    import re
    from datetime import datetime as _dt
    out = []
    for line in cal_text.split("\n"):
        # Strip the trailing (ID: ...) token
        line = re.sub(r"\s*\(ID:[^)]+\)\s*$", "", line).rstrip()
        # Match: "- YYYY-MM-DD[Thh:mm:ss[+-]hh:mm]: rest"
        m = re.match(r"^(\s*-\s*)(\d{4}-\d{2}-\d{2})(T\d{2}:\d{2}:\d{2}[+\-]\d{2}:\d{2})?:\s*(.*)$", line)
        if not m:
            out.append(line)
            continue
        prefix, date_part, time_part, rest = m.groups()
        try:
            if time_part:
                dt = _dt.fromisoformat(date_part + time_part)
                pretty = dt.strftime("%a %b %-d, %-I:%M %p")
            else:
                dt = _dt.strptime(date_part, "%Y-%m-%d")
                pretty = dt.strftime("%a %b %-d (all day)")
        except Exception:
            pretty = date_part + (time_part or "")
        out.append(f"{prefix}{pretty}: {rest}".rstrip())
    return "\n".join(out)


def _humanize_youtube(yt_text):
    """Collapse 'no change' day rows in the YouTube briefing section.
    The YouTube section already shows the channel summary; we just trim the
    'Recent videos' list down to those with actual deltas if any, otherwise
    keep the top-3."""
    if not yt_text or "Recent videos:" not in yt_text:
        return yt_text
    # Keep header + summary, then trim recent videos list to top 3
    header, _, rest = yt_text.partition("Recent videos:")
    lines = [l for l in rest.split("\n") if l.strip()]
    # Show only first 3 recent videos to reduce briefing length
    if len(lines) > 3:
        lines = lines[:3] + [f"  ({len(lines) - 3} more recent videos in YouTube Studio)"]
    return header.rstrip() + "\nRecent videos:\n" + "\n".join(lines)


def get_notion_todos_section(notion_query_db_fn):
    """Pull active to-dos from Sean's To-Do and active research from Sean's Research & Backlog.
    notion_query_db_fn: callable(database_id, max_results) returning Notion API JSON dict
    or None on error. Returns formatted markdown string, or empty string if no items."""
    TODO_DSID = "2692e075-ac64-8040-b028-d974d8f1e651"  # database id (was data_source id)
    RESEARCH_DSID = "07b36988-b1d7-498b-a8b7-f02831fff2a2"  # database id (was data_source id)
    PRIORITY_RANK = {"Now": 0, "This week": 1, "Someday": 2}
    EASTERN_DATE_FMT = "%a %b %-d"

    def _prop_select(props, key):
        v = props.get(key, {}) or {}
        sel = v.get("select") or {}
        return sel.get("name", "")

    def _prop_status(props, key):
        v = props.get(key, {}) or {}
        st = v.get("status") or {}
        return st.get("name", "")

    def _prop_title(props, key):
        v = props.get(key, {}) or {}
        arr = v.get("title") or []
        return "".join(t.get("plain_text", "") for t in arr).strip()

    def _prop_date_start(props, key):
        v = props.get(key, {}) or {}
        d = v.get("date") or {}
        return d.get("start", "") or ""

    def _humanize_due(iso):
        if not iso: return ""
        try:
            # date-only or datetime
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt.strftime("%a %b %-d")
        except Exception:
            return iso

    out_lines = []

    # --- To-Dos ---
    try:
        data = notion_query_db_fn(TODO_DSID, 100)
    except Exception as e:
        log.warning(f"notion to-do fetch failed: {e}")
        data = None
    todos = []
    if data:
        for row in data.get("results", []):
            props = row.get("properties", {})
            status = _prop_status(props, "Status")
            if status == "Done":
                continue
            todos.append({
                "task": _prop_title(props, "Task name"),
                "priority": _prop_select(props, "Priority") or "Someday",
                "category": _prop_select(props, "Category"),
                "due": _prop_date_start(props, "Due date"),
            })
    todos.sort(key=lambda t: (
        PRIORITY_RANK.get(t["priority"], 9),
        t["due"] or "9999-99-99",
    ))
    todos = todos[:12]
    if todos:
        out_lines.append("*To-Do (Notion):*")
        for t in todos:
            tags = [t["priority"]]
            if t["category"]: tags.append(t["category"])
            due_h = _humanize_due(t["due"])
            if due_h: tags.append(f"due {due_h}")
            tag_str = f" _({', '.join(tags)})_" if tags else ""
            out_lines.append(f"[ ] {t['task']}{tag_str}")

    # --- Research / Backlog ---
    try:
        rdata = notion_query_db_fn(RESEARCH_DSID, 50)
    except Exception as e:
        log.warning(f"notion research fetch failed: {e}")
        rdata = None
    research = []
    if rdata:
        for row in rdata.get("results", []):
            props = row.get("properties", {})
            if _prop_select(props, "Status") != "Active":
                continue
            research.append({
                "topic": _prop_title(props, "Topic"),
                "category": _prop_select(props, "Category"),
            })
    research = research[:5]
    if research:
        if out_lines: out_lines.append("")
        out_lines.append("*Research / Backlog:*")
        for r in research:
            cat = f" _({r['category']})_" if r["category"] else ""
            out_lines.append(f"[?] {r['topic']}{cat}")

    return "\n".join(out_lines)


def _humanize_tasks(tasks_text):
    """Pretty-print the next-run timestamp on scheduled tasks, e.g.
    '2026-05-04T09:00' -> 'Mon May 4, 9:00 AM'.
    """
    import re
    from datetime import datetime as _dt
    lines = []
    for line in tasks_text.split("\n"):
        m = re.search(r"next:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})", line)
        if m:
            try:
                dt = _dt.fromisoformat(m.group(1))
                pretty = dt.strftime("%a %b %-d, %-I:%M %p")
                line = line[:m.start()] + "next: " + pretty + line[m.end():]
            except Exception:
                pass
        lines.append(line)
    return "\n".join(lines)


async def build_briefing(gmail_get_unread, calendar_get_upcoming, check_important_emails=None, get_conn=None, notion_query_db_fn=None):
    # Run weather, calendar, YouTube, and money sections in parallel
    import youtube_stats
    import net_worth as _nw
    import plaid_recurring as _pr
    weather, cal, yt, money_summary, upcoming_bills = await asyncio.gather(
        get_weather(),
        asyncio.to_thread(calendar_get_upcoming, 5),
        asyncio.to_thread(youtube_stats.briefing_section),
        asyncio.to_thread(_nw.briefing_money_block),
        asyncio.to_thread(_pr.upcoming_bills_summary, 7),
    )
    yt = _humanize_youtube(yt)
    try:
        yt_comments_alert = await asyncio.to_thread(youtube_stats.comments_briefing_section)
        if yt_comments_alert:
            yt = yt + "\n" + yt_comments_alert
    except Exception:
        pass

    # Smart email section: tier-ranked, both Gmail + Outlook
    try:
        email = await build_smart_email_section()
    except Exception as e:
        email = "Email error: " + str(e)

    # Calendar error handling
    if "invalidscope" in str(cal).lower() or "bad request" in str(cal).lower():
        cal = "⚠️ Calendar unavailable — token needs refresh."
    else:
        cal = _humanize_calendar(cal)

    # Important email alerts
    alerts = None
    if check_important_emails:
        try:
            alerts = await asyncio.to_thread(check_important_emails)
        except: pass

    # To Do section
    todo_lines = []
    if get_conn:
        tasks = get_todo_tasks(get_conn)
        tasks = _humanize_tasks(tasks)
        todo_lines.append(f"*Scheduled / Reminders:*\n{tasks}")
    # Notion To-Do + Research/Backlog sections removed from briefing 2026-05-23
    # per Sean's request (this message is now scheduled/reminders only).
    # get_notion_todos_section() is left defined for easy re-enable.
    todo_section = "\n\n".join(todo_lines) if todo_lines else "Nothing scheduled."

    now = datetime.now(EASTERN).strftime("%A, %B %d, %Y")

    # Watched sources (House Projects, ONSR tracker, etc.) — failure-isolated
    # Health report emitted via Sysmon when any source fails (silent-degradation fix 2026-05-16).
    try:
        import briefing_sources as _bs
        watched_sections, _watched_health = _bs.render_watched_sources(
            notion_read_fn=_notion_read_for_briefing, return_health=True
        )
        # Sysmon heartbeat: only fires when something failed, to avoid OK-noise spam
        if _watched_health.get("failed") or _watched_health.get("unknown_type"):
            try:
                import os as _os, requests as _req
                _alert_token = _os.environ.get("ALERT_BOT_TOKEN", "")
                _alert_chat = _os.environ.get("ALERT_CHAT_ID", "")
                if _alert_token and _alert_chat:
                    lines = ["[BRIEFING-HEARTBEAT] watched-source failures detected:"]
                    for key, err in _watched_health.get("failed", []):
                        lines.append(f"  ❌ {key}: {err}")
                    for key in _watched_health.get("unknown_type", []):
                        lines.append(f"  ❔ {key}: unknown source type")
                    ok_count = len(_watched_health.get("ok", []))
                    empty_count = len(_watched_health.get("empty", []))
                    lines.append(f"OK: {ok_count}  empty: {empty_count}  failed: {len(_watched_health.get('failed', []))}")
                    _req.post(
                        f"https://api.telegram.org/bot{_alert_token}/sendMessage",
                        data={"chat_id": _alert_chat, "text": "\n".join(lines)[:4000]},
                        timeout=5,
                    )
            except Exception as _alert_e:
                import logging as _logging
                _logging.getLogger("clawdia.briefing").warning("briefing heartbeat alert failed: %s", _alert_e)
    except Exception as _e:
        import logging as _logging
        _logging.getLogger("clawdia.briefing").warning("watched_sources failed: %s", _e)
        watched_sections = []
    watched_block = "\n\n".join(watched_sections) if watched_sections else ""

    # Compose money section: net-worth one-liner + upcoming-bills summary if any
    money_lines = [money_summary]
    if upcoming_bills:
        money_lines.append("")
        money_lines.append(upcoming_bills)
    money_block = "\n".join(money_lines)

    briefing = (
        f"🌅 *Good morning, Sean!* — {now}\n\n"
        f"🌤 *Weather — North East, MD*\n{weather}\n\n"
        f"📅 *Your Day*\n{cal}\n\n"
        f"💰 *Money*\n{money_block}\n\n"
        + (f"{watched_block}\n\n" if watched_block else "")
        + f"🎵 *Hollowed Ground*\n{yt}\n\n"
        f"📬 *Unread Email*\n{email}\n\n"
        f"✅ *To Do*\n{todo_section}"
        + (f"\n\n🚨 *Important*\n{alerts}" if alerts else "")
    )
    # Hard safety cap: 12000 chars (~3 telegram messages). Caller is responsible
    # for chunking via _split_for_telegram before sending.
    if len(briefing) > 12000:
        briefing = briefing[:12000] + "\n\n_(truncated at 12000 chars)_"
    return briefing


def start_token_refresh_scheduler(refresh_google_fn, refresh_ms_fn):
    def loop():
        while True:
            time.sleep(3600)
            try:
                refresh_google_fn()
                refresh_ms_fn()
                log.info("Scheduled token refresh complete")
            except Exception as e:
                log.warning("Scheduled token refresh error: %s", e)
    t = threading.Thread(target=loop, daemon=True, name="token-refresh")
    t.start()
    log.info("Token refresh scheduler running — fires every hour")



def start_ram_monitor_scheduler(app, owner_id, threshold_pct=80, recovery_pct=70, interval_sec=900):
    """
    Monitor VPS RAM usage. Alert on Telegram when usage first crosses `threshold_pct`.
    Debounced: won't re-alert until usage drops below `recovery_pct` and crosses back up.
    Default: checks every 15 minutes, alerts at 80%, recovery threshold at 70%.
    """
    state = {"alerted": False}

    def get_mem_pct():
        try:
            out = subprocess.check_output(["free", "-m"], text=True, timeout=5)
            # Parse the "Mem:" line: total, used, free, shared, buff/cache, available
            for line in out.splitlines():
                if line.startswith("Mem:"):
                    parts = line.split()
                    total = int(parts[1])
                    used = int(parts[2])
                    available = int(parts[6]) if len(parts) > 6 else (total - used)
                    # "true used" = total - available (excludes buff/cache that can be reclaimed)
                    effective_used = total - available
                    return (effective_used / total) * 100, total, effective_used
        except Exception as e:
            log.warning("RAM monitor: could not read memory: %s", e)
        return None, None, None

    def loop():
        while True:
            time.sleep(interval_sec)
            pct, total, used = get_mem_pct()
            if pct is None:
                continue
            try:
                if pct >= threshold_pct and not state["alerted"]:
                    msg = (
                        f"[ALERT] Clawdia VPS RAM high: {pct:.1f}% ({used}MB / {total}MB used).\n"
                        f"Threshold: {threshold_pct}%. Won't re-alert until usage drops below {recovery_pct}%."
                    )
                    log.warning("RAM alert: %.1f%% (%dMB/%dMB)", pct, used, total)
                    if owner_id:
                        asyncio.run(app.bot.send_message(chat_id=owner_id, text=msg))
                    state["alerted"] = True
                elif pct < recovery_pct and state["alerted"]:
                    msg = f"[OK] Clawdia VPS RAM recovered: {pct:.1f}% ({used}MB / {total}MB)."
                    log.info("RAM recovered: %.1f%% (%dMB/%dMB)", pct, used, total)
                    if owner_id:
                        asyncio.run(app.bot.send_message(chat_id=owner_id, text=msg))
                    state["alerted"] = False
                else:
                    log.debug("RAM OK: %.1f%% (%dMB/%dMB)", pct, used, total)
            except Exception as e:
                log.warning("RAM monitor alert error: %s", e)

    t = threading.Thread(target=loop, daemon=True, name="ram-monitor")
    t.start()
    log.info("RAM monitor running - checks every %ds, alerts >=%d%%", interval_sec, threshold_pct)


def start_briefing_scheduler(app, owner_id, gmail_fn, calendar_fn, search_fn, check_important_fn=None, get_conn=None, notion_query_db_fn=None):
    async def send_briefing():
        log.info("Building morning briefing...")
        try:
            text = await build_briefing(gmail_fn, calendar_fn, check_important_fn, get_conn, notion_query_db_fn)
            # Chunk at paragraph boundaries to stay under Telegram's 4096-char cap.
            from bot_new import _split_for_telegram
            chunks = _split_for_telegram(text, limit=3900)
            n = len(chunks)
            for i, chunk in enumerate(chunks, 1):
                # Only prefix multi-message briefings; single-message stays clean.
                body = (f"({i}/{n}) " + chunk) if n > 1 else chunk
                await app.bot.send_message(chat_id=owner_id, text=body, parse_mode=None)
            log.info("Morning briefing sent (%d chunk%s).", n, "" if n == 1 else "s")
        except Exception as e:
            log.error("Briefing failed: %s", e)
            try:
                await app.bot.send_message(chat_id=owner_id, text=f"🐾 Morning briefing failed: {e}")
            except: pass

    def scheduler_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            now = datetime.now(EASTERN)
            target = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait = (target - now).total_seconds()
            log.info("Next briefing in %.0f seconds (%.1fh)", wait, wait / 3600)
            time.sleep(wait)
            loop.run_until_complete(send_briefing())

    t = threading.Thread(target=scheduler_loop, daemon=True, name="briefing-scheduler")
    t.start()
    log.info("Morning briefing scheduler running — fires at 09:00 Eastern daily")


def _nudges_init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS nudges_sent (
        event_id TEXT PRIMARY KEY,
        nudged_at TEXT NOT NULL,
        event_start TEXT NOT NULL
    )""")
    conn.commit()


def _is_quiet_hours(now_et):
    h = now_et.hour
    return h >= 22 or h < 6


def start_calendar_nudge_scheduler(app, owner_id, get_conn, lead_minutes=60, interval_sec=900):
    """
    Every interval_sec (default 15 min), scan Google Calendar for events
    starting within the next `lead_minutes`. For each NEW one (not yet
    nudged), send a Telegram message with title, time, and location.
    """
    import asyncio, threading, time, zoneinfo
    from datetime import datetime, timezone, timedelta
    from googleapiclient.discovery import build

    EASTERN = zoneinfo.ZoneInfo("America/New_York")
    log = logging.getLogger("clawdia.nudges")

    async def send_nudge(event):
        try:
            summary = event.get("summary", "(no title)")
            location = event.get("location", "")
            start_raw = event["start"].get("dateTime", event["start"].get("date"))
            try:
                import dateutil.parser as _dp
                start_dt = _dp.isoparse(start_raw)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                start_et = start_dt.astimezone(EASTERN)
                now_et = datetime.now(EASTERN)
                mins_until = int((start_et - now_et).total_seconds() / 60)
            except Exception:
                start_et = None
                mins_until = "?"

            lines = [
                "Upcoming: " + summary,
                "In ~" + str(mins_until) + " min (" + (start_et.strftime("%-I:%M %p %Z") if start_et else "?") + ")",
            ]
            if location:
                lines.append("Location: " + location)
                lines.append("Want me to check traffic? Just ask.")

            await app.bot.send_message(chat_id=owner_id, text=chr(10).join(lines))
        except Exception as e:
            log.error("send_nudge failed for event %s: %s", event.get("id","?"), e)

    def loop():
        import sys
        sys.path.insert(0, "/opt/clawdia")
        from bot_new import get_google_creds

        ev_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(ev_loop)
        log.info("Calendar nudge scheduler running (lead=%dmin, interval=%ds)", lead_minutes, interval_sec)

        while True:
            time.sleep(interval_sec)
            try:
                now_utc = datetime.now(timezone.utc)
                now_et = now_utc.astimezone(EASTERN)

                window_end = now_utc + timedelta(minutes=lead_minutes)
                svc = build("calendar", "v3", credentials=get_google_creds())
                events = svc.events().list(
                    calendarId="primary",
                    timeMin=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    timeMax=window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    singleEvents=True,
                    orderBy="startTime",
                ).execute().get("items", [])

                with get_conn() as conn:
                    _nudges_init(conn)
                    already_nudged = {r[0] for r in conn.execute("SELECT event_id FROM nudges_sent").fetchall()}

                for ev in events:
                    eid = ev.get("id")
                    if not eid or eid in already_nudged:
                        continue
                    if "dateTime" not in ev.get("start", {}):
                        continue
                    try:
                        import dateutil.parser as _dp
                        start_dt = _dp.isoparse(ev["start"]["dateTime"])
                        if start_dt.tzinfo is None:
                            start_dt = start_dt.replace(tzinfo=timezone.utc)
                        mins_until = int((start_dt - now_utc).total_seconds() / 60)
                    except Exception:
                        mins_until = lead_minutes
                    if _is_quiet_hours(now_et) and mins_until > 10:
                        continue

                    ev_loop.run_until_complete(send_nudge(ev))
                    with get_conn() as conn:
                        _nudges_init(conn)
                        conn.execute(
                            "INSERT OR REPLACE INTO nudges_sent (event_id, nudged_at, event_start) VALUES (?,?,?)",
                            (eid, now_utc.isoformat(), ev["start"]["dateTime"]),
                        )
                        conn.commit()
                    log.info("Nudge sent for %s (%s)", ev.get("summary","?"), eid)

                cutoff = (now_utc - timedelta(days=30)).isoformat()
                with get_conn() as conn:
                    _nudges_init(conn)
                    conn.execute("DELETE FROM nudges_sent WHERE nudged_at < ?", (cutoff,))
                    conn.commit()

            except Exception as e:
                log.error("Calendar nudge scheduler error: %s", e)

    threading.Thread(target=loop, daemon=True, name="calendar-nudges").start()

# === V2 SMARTER BRIEFING (added 2026-04-24) ===

VIP_SENDERS = [
    "heatherdurgin",         # wife
    "sudhir.pawar@oracle",   # manager (read-only ack — Oracle air-gap means
                             # we *flag* the sender but the body is still gated
                             # by the 1.Oracle label rule in the system prompt)
    "durginfamily",          # family account self-references
]

# Tier 1: Critical — drop everything keywords
CRITICAL_KEYWORDS = [
    "urgent", "action required", "deadline", "expir", "overdue",
    "bill due", "past due", "interview", "court", "subpoena",
    "fraud", "suspicious activity", "your account",
]

# Tier 2: Important — pay attention but not panicked keywords
IMPORTANT_KEYWORDS = [
    "invoice", "payment", "refund", "receipt", "statement",
    "appointment", "confirmation", "reservation", "rsvp",
    "shipped", "delivered", "out for delivery", "tracking",
]


def _tier_for_message(sender, subject):
    """Return ('critical' | 'important' | 'routine') based on heuristics."""
    s = (sender or "").lower()
    subj = (subject or "").lower()

    if any(k in subj for k in CRITICAL_KEYWORDS):
        return "critical"
    if any(v in s for v in VIP_SENDERS):
        return "important"
    if any(k in subj for k in IMPORTANT_KEYWORDS):
        return "important"
    return "routine"


def _classify_gmail_unread():
    """Pull up to 20 unread Gmail messages and bucket by tier."""
    try:
        import sys
        sys.path.insert(0, "/opt/clawdia")
        from bot_new import get_google_creds
        from googleapiclient.discovery import build
        svc = build("gmail", "v1", credentials=get_google_creds())
        msg_ids = svc.users().messages().list(
            userId="me", labelIds=["INBOX", "UNREAD"], maxResults=20
        ).execute().get("messages", [])
        if not msg_ids:
            return {"critical": [], "important": [], "routine_count": 0, "routine_preview": [], "source": "Gmail"}
        critical, important = [], []
        routine_count = 0
        routine_preview = []
        for m in msg_ids:
            full = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject"]
            ).execute()
            hdrs = {h["name"]: h["value"] for h in full["payload"]["headers"]}
            sender = hdrs.get("From", "?")
            subject = hdrs.get("Subject", "(no subject)")
            tier = _tier_for_message(sender, subject)
            short_sender = sender.split("<")[0].strip()
            short_sender = short_sender.strip('"')
            entry = "  - " + short_sender + ": " + subject[:80]
            if tier == "critical":
                critical.append(entry)
            elif tier == "important":
                important.append(entry)
            else:
                routine_count += 1
                preview = short_sender + ": " + subject[:60] + " [Gmail]"
                routine_preview.append(preview)
        return {"critical": critical, "important": important, "routine_count": routine_count,
                "routine_preview": routine_preview, "source": "Gmail"}
    except Exception as e:
        log.error("Gmail classify failed: %s", e)
        return {"critical": [], "important": [], "routine_count": 0, "routine_preview": [], "source": "Gmail", "error": str(e)[:100]}


def _classify_outlook_unread():
    """MS_DEPRECATED 2026-05-07: Outlook integration removed (Azure app deleted).
    Returns empty buckets so build_smart_email_section continues with Gmail only."""
    return {"critical": [], "important": [], "routine_count": 0,
            "routine_preview": [], "source": "Outlook"}


async def build_smart_email_section():
    """Combined, prioritized email section across Gmail + Outlook."""
    gmail_buckets, outlook_buckets = await asyncio.gather(
        asyncio.to_thread(_classify_gmail_unread),
        asyncio.to_thread(_classify_outlook_unread),
    )

    lines = []
    crit_total = len(gmail_buckets["critical"]) + len(outlook_buckets["critical"])
    imp_total = len(gmail_buckets["important"]) + len(outlook_buckets["important"])
    routine_total = gmail_buckets["routine_count"] + outlook_buckets["routine_count"]
    grand_total = crit_total + imp_total + routine_total

    if grand_total == 0:
        return "Inbox zero across Gmail and Outlook. Enjoy."

    lines.append("Total unread: " + str(grand_total) + " (Gmail + Outlook)")

    if crit_total:
        lines.append("")
        lines.append("CRITICAL (" + str(crit_total) + "):")
        for e in gmail_buckets["critical"]: lines.append(e + " [Gmail]")
        for e in outlook_buckets["critical"]: lines.append(e + " [Outlook]")

    if imp_total:
        lines.append("")
        lines.append("Important (" + str(imp_total) + "):")
        for e in gmail_buckets["important"]: lines.append(e + " [Gmail]")
        for e in outlook_buckets["important"]: lines.append(e + " [Outlook]")

    if routine_total:
        lines.append("")
        lines.append("+ " + str(routine_total) + " routine:")
        # Show top 3 routine senders so Sean has a clue what is in there
        routine_previews = (gmail_buckets.get("routine_preview", []) +
                           outlook_buckets.get("routine_preview", []))
        for prev in routine_previews[:3]:
            lines.append("  · " + prev)
        if len(routine_previews) > 3:
            lines.append("  · (and " + str(len(routine_previews) - 3) + " more)")

    # Surface error notices subtly so a token issue doesn't go unnoticed
    for buckets in (gmail_buckets, outlook_buckets):
        if buckets.get("error"):
            lines.append("")
            lines.append("(" + buckets["source"] + " error: " + buckets["error"] + ")")

    return chr(10).join(lines)
