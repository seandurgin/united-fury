#!/usr/bin/env python3
"""Morning briefing module for Clawdia."""
import asyncio, logging, threading, time
from datetime import datetime, timezone, timedelta
import zoneinfo
import httpx

log = logging.getLogger("clawdia.briefing")

EASTERN = zoneinfo.ZoneInfo("America/New_York")  # Auto-adjusts EST/EDT

async def get_weather():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://wttr.in/North+East,MD",
                params={"format": "3"},
                headers={"User-Agent": "Clawdia/1.0"}
            )
            return r.text.strip()
    except Exception as e:
        return f"Weather unavailable: {e}"

async def build_briefing(gmail_get_unread, calendar_get_upcoming, brave_search):
    weather, news, email, cal = await asyncio.gather(
        get_weather(),
        brave_search("major news headlines today", 5),
        asyncio.to_thread(gmail_get_unread, 5),
        asyncio.to_thread(calendar_get_upcoming, 5),
    )
    now = datetime.now(EASTERN).strftime("%A, %B %d, %Y")
    briefing = (
        f"🌅 *Good morning, Sean!* — {now}\n\n"
        f"🌤 *Weather — North East, MD*\n{weather}\n\n"
        f"📅 *Your Day*\n{cal}\n\n"
        f"📬 *Unread Email*\n{email}\n\n"
        f"📰 *Major News*\n{news}"
    )
    if len(briefing) > 4000:
        briefing = briefing[:4000] + "\n\n_(truncated — ask me for more)_"
    return briefing

def start_briefing_scheduler(app, owner_id, gmail_fn, calendar_fn, search_fn):
    """Start background thread that sends briefing at 9:00 AM Eastern daily."""

    async def send_briefing():
        log.info("Building morning briefing...")
        try:
            text = await build_briefing(gmail_fn, calendar_fn, search_fn)
            await app.bot.send_message(chat_id=owner_id, text=text, parse_mode="Markdown")
            log.info("Morning briefing sent.")
        except Exception as e:
            log.error("Briefing failed: %s", e)
            try:
                await app.bot.send_message(chat_id=owner_id, text=f"🐾 Morning briefing failed: {e}")
            except:
                pass

    def scheduler_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            now = datetime.now(EASTERN)
            target = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if now >= target:
                # Past 9am, wait until tomorrow
                target = target.replace(day=target.day + 1)
            wait = (target - now).total_seconds()
            log.info("Next briefing in %.0f seconds (%.1fh)", wait, wait / 3600)
            time.sleep(wait)
            loop.run_until_complete(send_briefing())

    t = threading.Thread(target=scheduler_loop, daemon=True, name="briefing-scheduler")
    t.start()
    log.info("Morning briefing scheduler running — fires at 09:00 Eastern daily")
