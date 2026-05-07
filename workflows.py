#!/usr/bin/env python3
"""
Multi-step workflows for Clawdia.

A workflow is a named sequence of free-form prompts (steps).
Each step is fed to ask_claude with the previous step's output as context.
The final result is sent to Sean via Telegram.

Schedule grammar reuses tasks.py: "every day", "every monday", "every friday",
"hourly", "weekly", or fallback to daily 9 AM ET.
"""
import asyncio, json, logging, threading, time, zoneinfo
from datetime import datetime, timedelta

log = logging.getLogger("clawdia.workflows")
EASTERN = zoneinfo.ZoneInfo("America/New_York")


def workflow_calc_next(schedule):
    """Mirror of tasks.task_calc_next. Returns ISO datetime in ET for next run."""
    now = datetime.now(EASTERN)
    s = (schedule or "").lower().strip()
    if "monday" in s:
        days = (0 - now.weekday()) % 7 or 7
        return (now + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    if "friday" in s:
        days = (4 - now.weekday()) % 7 or 7
        return (now + timedelta(days=days)).replace(hour=17, minute=0, second=0, microsecond=0).isoformat()
    if "hourly" in s or "every hour" in s:
        return (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).isoformat()
    if "weekly" in s or "every week" in s:
        return (now + timedelta(weeks=1)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    # default: daily 9 AM ET
    next_run = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return next_run.isoformat()


def workflows_init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS workflows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        schedule TEXT NOT NULL,
        steps_json TEXT NOT NULL,
        active INTEGER DEFAULT 1,
        paused INTEGER DEFAULT 0,
        last_run TEXT,
        next_run TEXT,
        created TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()


def workflow_add(get_conn, name, schedule, steps):
    """steps: list of strings (free-form prompts)."""
    if not isinstance(steps, list) or not steps:
        return "Workflow add failed: need at least one step."
    if len(steps) > 10:
        return "Workflow add failed: max 10 steps per workflow."
    with get_conn() as conn:
        workflows_init(conn)
        next_run = workflow_calc_next(schedule)
        cursor = conn.execute(
            "INSERT INTO workflows (name, schedule, steps_json, next_run) VALUES (?, ?, ?, ?)",
            (name, schedule, json.dumps(steps), next_run),
        )
        wid = cursor.lastrowid
        conn.commit()
    return f"Workflow [{wid}] added: \"{name}\" ({len(steps)} steps, schedule: {schedule}). Next run: {next_run[:16]}"


def workflow_list(get_conn):
    with get_conn() as conn:
        workflows_init(conn)
        rows = conn.execute(
            "SELECT id, name, schedule, paused, next_run, steps_json FROM workflows WHERE active=1 ORDER BY paused, next_run"
        ).fetchall()
    if not rows:
        return "No workflows defined. Use /workflow add to create one."
    lines = ["Workflows:"]
    for r in rows:
        wid, name, schedule, paused, next_run, steps_json = r
        marker = " [PAUSED]" if paused else ""
        try:
            n_steps = len(json.loads(steps_json))
        except Exception:
            n_steps = "?"
        lines.append(f"[{wid}]{marker} \"{name}\" — {schedule}, {n_steps} steps (next: {(next_run or '?')[:16]})")
    return chr(10).join(lines)


def workflow_show(get_conn, workflow_id):
    with get_conn() as conn:
        workflows_init(conn)
        row = conn.execute(
            "SELECT id, name, schedule, paused, last_run, next_run, steps_json FROM workflows WHERE id=? AND active=1",
            (workflow_id,),
        ).fetchone()
    if not row:
        return f"Workflow {workflow_id} not found."
    wid, name, schedule, paused, last_run, next_run, steps_json = row
    try:
        steps = json.loads(steps_json)
    except Exception:
        steps = []
    lines = [
        f"Workflow [{wid}]: {name}",
        f"Schedule: {schedule}",
        f"Paused: {'yes' if paused else 'no'}",
        f"Last run: {last_run or 'never'}",
        f"Next run: {next_run[:16] if next_run else '?'}",
        "",
        "Steps:",
    ]
    for i, step in enumerate(steps, 1):
        lines.append(f"  {i}. {step}")
    return chr(10).join(lines)


def workflow_delete(get_conn, workflow_id):
    with get_conn() as conn:
        workflows_init(conn)
        conn.execute("UPDATE workflows SET active=0 WHERE id=?", (workflow_id,))
        conn.commit()
    return f"Workflow {workflow_id} deleted."


def workflow_pause(get_conn, workflow_id):
    with get_conn() as conn:
        workflows_init(conn)
        row = conn.execute("SELECT name FROM workflows WHERE id=? AND active=1", (workflow_id,)).fetchone()
        if not row:
            return f"Workflow {workflow_id} not found."
        conn.execute("UPDATE workflows SET paused=1 WHERE id=?", (workflow_id,))
        conn.commit()
    return f"Workflow {workflow_id} paused: \"{row[0]}\". Use /workflow resume {workflow_id} to re-enable."


def workflow_resume(get_conn, workflow_id):
    with get_conn() as conn:
        workflows_init(conn)
        row = conn.execute("SELECT name, schedule FROM workflows WHERE id=? AND active=1", (workflow_id,)).fetchone()
        if not row:
            return f"Workflow {workflow_id} not found."
        new_next = workflow_calc_next(row[1])
        conn.execute("UPDATE workflows SET paused=0, next_run=? WHERE id=?", (new_next, workflow_id))
        conn.commit()
    return f"Workflow {workflow_id} resumed: \"{row[0]}\". Next run: {new_next[:16]}"


async def workflow_execute(workflow_id, get_conn, ask_claude_fn, owner_id):
    """
    Run all steps of a workflow in sequence.
    Each step's output is appended as context to the next step's prompt.
    Returns the final result string.
    """
    with get_conn() as conn:
        workflows_init(conn)
        row = conn.execute(
            "SELECT name, steps_json FROM workflows WHERE id=? AND active=1",
            (workflow_id,),
        ).fetchone()
    if not row:
        return f"Workflow {workflow_id} not found."
    name, steps_json = row
    try:
        steps = json.loads(steps_json)
    except Exception as e:
        return f"Workflow {workflow_id} steps_json invalid: {e}"

    log.info("Executing workflow [%d] %s with %d steps", workflow_id, name, len(steps))
    accumulated_context = ""
    step_outputs = []

    for i, step_prompt in enumerate(steps, 1):
        if accumulated_context:
            full_prompt = (
                f"[Workflow '{name}' step {i}/{len(steps)}]\n\n"
                f"[Previous step output]:\n{accumulated_context[:2000]}\n\n"
                f"[Current step instruction]: {step_prompt}"
            )
        else:
            full_prompt = f"[Workflow '{name}' step {i}/{len(steps)}]\n\n{step_prompt}"

        try:
            output = await ask_claude_fn(owner_id, full_prompt)
            step_outputs.append(f"--- Step {i}: {step_prompt[:60]} ---\n{output}")
            accumulated_context = output  # Only carry forward the LAST step's output
            log.info("Workflow [%d] step %d done (%d chars)", workflow_id, i, len(output))
        except Exception as e:
            step_outputs.append(f"--- Step {i} FAILED ---\n{e}")
            log.error("Workflow [%d] step %d failed: %s", workflow_id, i, e)
            break

    # Update last_run and next_run
    with get_conn() as conn:
        workflows_init(conn)
        # Need to refetch schedule since it might have been updated
        sched_row = conn.execute("SELECT schedule FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        if sched_row:
            new_next = workflow_calc_next(sched_row[0])
            conn.execute(
                "UPDATE workflows SET last_run=?, next_run=? WHERE id=?",
                (datetime.now(EASTERN).isoformat(), new_next, workflow_id),
            )
            conn.commit()

    summary = f"Workflow \"{name}\" complete:\n\n" + chr(10).join(step_outputs)
    return summary


def workflow_run_now(get_conn, ask_claude_fn, owner_id, workflow_id):
    """Sync wrapper for /workflow run command."""
    return asyncio.run(workflow_execute(workflow_id, get_conn, ask_claude_fn, owner_id))


def start_workflow_scheduler(app, owner_id, get_conn, ask_claude_fn):
    """Background thread that polls every 60 sec and fires due workflows."""
    async def run_due_workflow(wid):
        try:
            result = await workflow_execute(wid, get_conn, ask_claude_fn, owner_id)
            # Telegram chunks at 4000 chars
            for i in range(0, len(result), 4000):
                await app.bot.send_message(chat_id=owner_id, text=result[i:i+4000])
        except Exception as e:
            log.error("Workflow %d run failed: %s", wid, e)
            try:
                await app.bot.send_message(chat_id=owner_id, text=f"Workflow {wid} failed: {e}")
            except: pass

    def loop():
        ev = asyncio.new_event_loop()
        asyncio.set_event_loop(ev)
        log.info("Workflow scheduler running (60s tick)")
        while True:
            time.sleep(60)
            try:
                now_iso = datetime.now(EASTERN).isoformat()
                with get_conn() as conn:
                    workflows_init(conn)
                    due = conn.execute(
                        "SELECT id FROM workflows WHERE active=1 AND paused=0 AND next_run<=?",
                        (now_iso,),
                    ).fetchall()
                for (wid,) in due:
                    ev.run_until_complete(run_due_workflow(wid))
            except Exception as e:
                log.error("Workflow scheduler loop error: %s", e)

    threading.Thread(target=loop, daemon=True, name="workflow-scheduler").start()
