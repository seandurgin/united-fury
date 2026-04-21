#!/usr/bin/env python3
"""Morning briefing module for Clawdia."""
import asyncio, logging, threading, time
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

    # Email with error handling
    try:
        email = await asyncio.to_thread(gmail_get_unread, 5)
        if "invalidscope" in str(email).lower() or "bad request" in str(email).lower():
            email = "⚠️ Email unavailable — token needs refresh. Run: systemctl restart clawdia"
    except Exception as e:
        email = f"⚠️ Email error: {e}"

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
