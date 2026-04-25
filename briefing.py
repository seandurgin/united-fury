#!/usr/bin/env python3
"""Morning briefing module for Clawdia."""
import asyncio, logging, threading, time, subprocess
from datetime import datetime, timedelta
import zoneinfo
import httpx

log = logging.getLogger("clawdia.briefing")
EASTERN = zoneinfo.ZoneInfo("America/New_York")

# OneNote Daily To Do page ID
TODO_ONENOTE_SECTION = "Daily To Do"

async def get_weather():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://wttr.in/North+East,MD",
                params={"format": "3"},
                headers={"User-Agent": "curl/7.68.0"}
            )
            text = r.text.strip()
            if '<' in text:
                import re
                text = re.sub(r'<[^>]+>', '', text).strip()
                text = text[:200] if text else "Weather unavailable"
            return text
    except Exception as e:
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
            lines.append(f"• [{row[0]}] {row[2]} ({row[1]}) — next: {next_run}")
        return "\n".join(lines)
    except Exception as e:
        return f"Tasks unavailable: {e}"

def get_onenote_todo(onenote_search_fn):
    """Pull To Do items from OneNote Daily To Do section."""
    try:
        result = onenote_search_fn("Daily To Do")
        if not result or "error" in result.lower():
            return None
        # Extract just the first 500 chars to keep briefing tight
        return result[:500]
    except Exception as e:
        return None

async def build_briefing(gmail_get_unread, calendar_get_upcoming, check_important_emails=None, get_conn=None, onenote_search_fn=None):
    # Run weather and calendar in parallel — skip news entirely
    weather, cal = await asyncio.gather(
        get_weather(),
        asyncio.to_thread(calendar_get_upcoming, 5),
    )

    # Smart email section: tier-ranked, both Gmail + Outlook
    try:
        email = await build_smart_email_section()
    except Exception as e:
        email = "Email error: " + str(e)

    # Calendar error handling
    if "invalidscope" in str(cal).lower() or "bad request" in str(cal).lower():
        cal = "⚠️ Calendar unavailable — token needs refresh."

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
        todo_lines.append(f"*Scheduled Tasks:*\n{tasks}")
    if onenote_search_fn:
        onenote_todo = get_onenote_todo(onenote_search_fn)
        if onenote_todo:
            todo_lines.append(f"*OneNote To Do:*\n{onenote_todo}")
    todo_section = "\n\n".join(todo_lines) if todo_lines else "Nothing scheduled."

    now = datetime.now(EASTERN).strftime("%A, %B %d, %Y")
    briefing = (
        f"🌅 *Good morning, Sean!* — {now}\n\n"
        f"🌤 *Weather — North East, MD*\n{weather}\n\n"
        f"📅 *Your Day*\n{cal}\n\n"
        f"📬 *Unread Email*\n{email}\n\n"
        f"✅ *To Do*\n{todo_section}"
        + (f"\n\n🚨 *Important*\n{alerts}" if alerts else "")
    )
    if len(briefing) > 4000:
        briefing = briefing[:4000] + "\n\n_(truncated)_"
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


def start_briefing_scheduler(app, owner_id, gmail_fn, calendar_fn, search_fn, check_important_fn=None, get_conn=None, onenote_search_fn=None):
    async def send_briefing():
        log.info("Building morning briefing...")
        try:
            text = await build_briefing(gmail_fn, calendar_fn, check_important_fn, get_conn, onenote_search_fn)
            await app.bot.send_message(chat_id=owner_id, text=text, parse_mode="Markdown")
            log.info("Morning briefing sent.")
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
            return {"critical": [], "important": [], "routine_count": 0, "source": "Gmail"}
        critical, important = [], []
        routine_count = 0
        for m in msg_ids:
            full = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject"]
            ).execute()
            hdrs = {h["name"]: h["value"] for h in full["payload"]["headers"]}
            sender = hdrs.get("From", "?")
            subject = hdrs.get("Subject", "(no subject)")
            tier = _tier_for_message(sender, subject)
            entry = "  - " + sender.split("<")[0].strip() + ": " + subject[:80]
            if tier == "critical":
                critical.append(entry)
            elif tier == "important":
                important.append(entry)
            else:
                routine_count += 1
        return {"critical": critical, "important": important, "routine_count": routine_count, "source": "Gmail"}
    except Exception as e:
        log.error("Gmail classify failed: %s", e)
        return {"critical": [], "important": [], "routine_count": 0, "source": "Gmail", "error": str(e)[:100]}


def _classify_outlook_unread():
    """Pull up to 20 unread Outlook messages and bucket by tier."""
    try:
        import sys
        sys.path.insert(0, "/opt/clawdia")
        from bot_new import ms_get
        params = {
            "$filter": "isRead eq false",
            "$top": 20,
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,receivedDateTime",
        }
        data = ms_get("/me/mailFolders/inbox/messages", params=params)
        msgs = data.get("value", [])
        if not msgs:
            return {"critical": [], "important": [], "routine_count": 0, "source": "Outlook"}
        critical, important = [], []
        routine_count = 0
        for m in msgs:
            sender_obj = (m.get("from") or {}).get("emailAddress", {})
            sender_name = sender_obj.get("name", "?")
            sender_addr = sender_obj.get("address", "?")
            sender = sender_name + " <" + sender_addr + ">"
            subject = m.get("subject", "(no subject)")
            tier = _tier_for_message(sender, subject)
            entry = "  - " + sender_name + ": " + subject[:80]
            if tier == "critical":
                critical.append(entry)
            elif tier == "important":
                important.append(entry)
            else:
                routine_count += 1
        return {"critical": critical, "important": important, "routine_count": routine_count, "source": "Outlook"}
    except Exception as e:
        log.error("Outlook classify failed: %s", e)
        return {"critical": [], "important": [], "routine_count": 0, "source": "Outlook", "error": str(e)[:100]}


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
        lines.append("+ " + str(routine_total) + " routine (newsletters, automated, etc.)")

    # Surface error notices subtly so a token issue doesn't go unnoticed
    for buckets in (gmail_buckets, outlook_buckets):
        if buckets.get("error"):
            lines.append("")
            lines.append("(" + buckets["source"] + " error: " + buckets["error"] + ")")

    return chr(10).join(lines)
