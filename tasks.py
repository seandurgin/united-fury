#!/usr/bin/env python3
import asyncio, logging, threading, time, zoneinfo
from datetime import datetime, timedelta

log = logging.getLogger("clawdia.tasks")
EASTERN = zoneinfo.ZoneInfo("America/New_York")

def task_calc_next(schedule):
    now = datetime.now(EASTERN)
    s = schedule.lower().strip()
    if s.startswith('once:'):
        return schedule.split(':', 1)[1]
    if 'monday' in s:
        days = (0 - now.weekday()) % 7 or 7
        return (now + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    elif 'friday' in s:
        days = (4 - now.weekday()) % 7 or 7
        return (now + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    elif 'hourly' in s or 'every hour' in s:
        return (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).isoformat()
    elif 'weekly' in s or 'every week' in s:
        return (now + timedelta(weeks=1)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    else:
        next_run = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if next_run <= now: next_run += timedelta(days=1)
        return next_run.isoformat()

def tasks_init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS scheduled_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        schedule TEXT NOT NULL, prompt TEXT NOT NULL,
        last_run TEXT, next_run TEXT,
        created TEXT DEFAULT CURRENT_TIMESTAMP, active INTEGER DEFAULT 1,
        paused INTEGER DEFAULT 0)""")
    # Migration: add `paused` column if it does not exist (for existing databases)
    try:
        conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN paused INTEGER DEFAULT 0")
    except Exception:
        pass  # Column already exists
    conn.commit()

def task_add(get_conn, schedule, prompt):
    with get_conn() as conn:
        tasks_init(conn)
        next_run = task_calc_next(schedule)
        conn.execute("INSERT INTO scheduled_tasks (schedule,prompt,next_run) VALUES (?,?,?)", (schedule,prompt,next_run))
        conn.commit()
    return f'Scheduled: "{prompt}" — {schedule}. Next: {next_run[:16]}'

def task_list(get_conn):
    with get_conn() as conn:
        tasks_init(conn)
        rows = conn.execute("SELECT id,schedule,prompt,next_run,paused FROM scheduled_tasks WHERE active=1 ORDER BY paused, next_run").fetchall()
    if not rows: return "No scheduled tasks."
    lines = []
    for r in rows:
        paused = " [PAUSED]" if r[4] else ""
        lines.append(f"[{r[0]}]{paused} {r[1]}: {r[2]} (next: {(r[3] or '?')[:16]})")
    return "Scheduled tasks:\n" + "\n".join(lines)

def task_delete(get_conn, task_id):
    with get_conn() as conn:
        tasks_init(conn)
        conn.execute("UPDATE scheduled_tasks SET active=0 WHERE id=?", (task_id,))
        conn.commit()
    return f"Task {task_id} deleted."

def task_pause(get_conn, task_id):
    with get_conn() as conn:
        tasks_init(conn)
        row = conn.execute("SELECT id, prompt FROM scheduled_tasks WHERE id=? AND active=1", (task_id,)).fetchone()
        if not row:
            return f"Task {task_id} not found (or already deleted)."
        conn.execute("UPDATE scheduled_tasks SET paused=1 WHERE id=?", (task_id,))
        conn.commit()
    return f"Task {task_id} paused: \"{row[1]}\". Use /task resume {task_id} to re-enable."

def task_resume(get_conn, task_id):
    with get_conn() as conn:
        tasks_init(conn)
        row = conn.execute("SELECT id, schedule, prompt FROM scheduled_tasks WHERE id=? AND active=1", (task_id,)).fetchone()
        if not row:
            return f"Task {task_id} not found (or deleted)."
        # Recalc next_run so it fires on the next scheduled slot, not whenever it was paused
        new_next = task_calc_next(row[1])
        conn.execute("UPDATE scheduled_tasks SET paused=0, next_run=? WHERE id=?", (new_next, task_id))
        conn.commit()
    return f"Task {task_id} resumed: \"{row[2]}\". Next run: {new_next[:16]}"


def start_task_scheduler(app, owner_id, get_conn, ask_claude_fn):
    async def run_task(task_id, prompt, schedule):
        try:
            is_once = schedule.lower().strip().startswith('once:')
            if is_once:
                reply = await ask_claude_fn(owner_id, f"[Reminder fired] {prompt}")
                await app.bot.send_message(chat_id=owner_id, text=f"⏰ Reminder: {prompt}\n\n{reply}")
            else:
                reply = await ask_claude_fn(owner_id, f"[Scheduled task] {prompt}")
                await app.bot.send_message(chat_id=owner_id, text=f"Scheduled: {prompt}\n\n{reply}")
            with get_conn() as conn:
                if is_once:
                    conn.execute("UPDATE scheduled_tasks SET last_run=?, active=0 WHERE id=?",
                                 (datetime.now(EASTERN).isoformat(), task_id))
                else:
                    conn.execute("UPDATE scheduled_tasks SET last_run=?,next_run=? WHERE id=?",
                                 (datetime.now(EASTERN).isoformat(), task_calc_next(schedule), task_id))
                conn.commit()
        except Exception as e:
            log.error("Task %d failed: %s", task_id, e)

    def loop():
        ev = asyncio.new_event_loop()
        asyncio.set_event_loop(ev)
        while True:
            time.sleep(60)
            try:
                now = datetime.now(EASTERN).isoformat()
                with get_conn() as conn:
                    tasks_init(conn)
                    due = conn.execute("SELECT id,prompt,schedule FROM scheduled_tasks WHERE active=1 AND paused=0 AND next_run<=?", (now,)).fetchall()
                for t in due:
                    ev.run_until_complete(run_task(t[0], t[1], t[2]))
            except Exception as e:
                log.error("Task scheduler error: %s", e)

    threading.Thread(target=loop, daemon=True, name="task-scheduler").start()
    log.info("Task scheduler running")
