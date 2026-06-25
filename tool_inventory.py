"""Sync Clawdia's live TOOLS list to a Notion database.

Idempotent — adds new tools, updates descriptions/categories of existing ones,
marks removed tools as Archived (never deletes, so historical record is preserved).

On first call, creates the database under "Sean's HQ" page. Subsequent calls
reuse the saved database ID from memory.

Shipped 2026-06-24. Author: dev-Claude session, deployed via clawdia_ssh.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

import requests

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

PARENT_PAGE_ID = "3532e075-ac64-81f6-afbb-cb314763ba07"  # Sean's HQ

DB_PATH = os.environ.get("DB_PATH", "/var/lib/clawdia/memory.db")
DB_ID_CATEGORY = "clawdia"
DB_ID_KEY = "tool_inventory_db_id"

# Category rules — first match wins. Tune as new tool families are added.
CATEGORY_RULES: list[tuple[str, str]] = [
    (r"^family_gmail_", "Email — Family Gmail"),
    (r"^gmail_", "Email — Gmail"),
    (r"^outlook_", "Email — Outlook"),
    (r"^icloud_mail_", "Email — iCloud"),
    (r"^email_scan", "Email — Meta"),
    (r"^imessage_", "iMessage"),
    (r"^icloud_calendar", "Calendar — iCloud"),
    (r"^calendar_", "Calendar — Google"),
    (r"^check_availability", "Calendar — Cross"),
    (r"^family_drive_", "Files — Family Drive"),
    (r"^drive_", "Files — Drive"),
    (r"^create_google_(doc|sheet)|^create_spreadsheet", "Files — Doc Creation"),
    (r"^pdf_", "Files — PDF"),
    (r"^notes_", "Notes — Apple"),
    (r"^onenote_", "Notes — OneNote"),
    (r"^notion_", "Knowledge — Notion"),
    (r"^save_memory|^delete_memory", "Memory"),
    (r"^web_(search|fetch|price)", "Web"),
    (r"^x_lookup", "Social — X"),
    (r"^plaid_", "Finance — Plaid"),
    (r"^net_worth|^update_(asset|debt)|^debt_", "Finance — Custom"),
    (r"^reminders_|^remind_me", "Productivity — Reminders"),
    (r"^contacts_", "Productivity — Contacts"),
    (r"^youtube_", "Media — YouTube"),
    (r"^maps_|^weather", "Maps & Weather"),
    (r"^marketplace_", "Marketplace"),
    (r"^location_", "Location"),
    (r"^unifi_", "Network — UniFi"),
    (r"^generate_image|^image_", "Image Generation"),
    (r"^clawdia_ssh|^host_exec", "Privileged Shell"),
    (r"^current_time|^apify_status|^cost_summary|^commute_eta|^tool_inventory", "System State"),
]


def _categorize(name: str) -> str:
    for pattern, category in CATEGORY_RULES:
        if re.match(pattern, name):
            return category
    return "Uncategorized"


def _headers() -> dict:
    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        raise RuntimeError("NOTION_TOKEN is not set in env")
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _get_memory(category: str, key: str) -> str | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT value FROM memory WHERE category=? COLLATE NOCASE AND key=? COLLATE NOCASE",
            (category, key),
        ).fetchone()
    return row[0] if row else None


def _save_memory(category: str, key: str, value: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO memory (category, key, value, created, updated, tier) "
            "VALUES (?, ?, ?, ?, ?, 'core') "
            "ON CONFLICT(category, key) DO UPDATE SET "
            "value=excluded.value, updated=excluded.updated",
            (category, key, value, now, now),
        )


def _all_categories() -> list[dict]:
    """Return the unique set of category names as Notion select options."""
    seen: dict[str, None] = {}
    for _, cat in CATEGORY_RULES:
        seen[cat] = None
    seen["Uncategorized"] = None
    return [{"name": c} for c in seen.keys()]


def _create_database() -> str:
    """Create the Notion DB under Sean's HQ. Return its ID."""
    payload = {
        "parent": {"type": "page_id", "page_id": PARENT_PAGE_ID},
        "icon": {"type": "emoji", "emoji": "🧰"},
        "title": [{"type": "text", "text": {"content": "Clawdia Tool Inventory"}}],
        "description": [
            {
                "type": "text",
                "text": {
                    "content": "Auto-synced from the live TOOLS list in bot_new.py via the tool_inventory_sync tool. Active = currently wired in. Archived = previously existed, now removed. Descriptions truncated at 2000 chars per Notion limit."
                },
            }
        ],
        "properties": {
            "Name": {"title": {}},
            "Category": {"select": {"options": _all_categories()}},
            "Description": {"rich_text": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "Active", "color": "green"},
                        {"name": "Archived", "color": "gray"},
                    ]
                }
            },
            "Last Synced": {"date": {}},
        },
    }
    r = requests.post(f"{NOTION_API}/databases", headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def _query_existing(db_id: str) -> dict[str, str]:
    """Return {tool_name: page_id} for all existing rows in the DB."""
    existing: dict[str, str] = {}
    cursor: str | None = None
    while True:
        body: dict[str, Any] = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(
            f"{NOTION_API}/databases/{db_id}/query",
            headers=_headers(),
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        for page in data.get("results", []):
            title_prop = page.get("properties", {}).get("Name", {}).get("title", [])
            name = title_prop[0]["plain_text"] if title_prop else ""
            if name:
                existing[name] = page["id"]
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return existing


def _build_props(tool: dict, category: str, status: str, synced_at: str) -> dict:
    desc = (tool.get("description") or "")[:2000]
    return {
        "Name": {"title": [{"text": {"content": tool["name"]}}]},
        "Category": {"select": {"name": category}},
        "Description": {"rich_text": [{"text": {"content": desc}}]},
        "Status": {"select": {"name": status}},
        "Last Synced": {"date": {"start": synced_at}},
    }


def _create_row(db_id: str, tool: dict, category: str, synced_at: str) -> None:
    payload = {
        "parent": {"database_id": db_id},
        "properties": _build_props(tool, category, "Active", synced_at),
    }
    r = requests.post(f"{NOTION_API}/pages", headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()


def _update_row(page_id: str, tool: dict, category: str, status: str, synced_at: str) -> None:
    payload = {"properties": _build_props(tool, category, status, synced_at)}
    r = requests.patch(f"{NOTION_API}/pages/{page_id}", headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()


def _archive_row(page_id: str, synced_at: str) -> None:
    payload = {
        "properties": {
            "Status": {"select": {"name": "Archived"}},
            "Last Synced": {"date": {"start": synced_at}},
        }
    }
    r = requests.patch(f"{NOTION_API}/pages/{page_id}", headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()


def sync(tools_list: list) -> str:
    """Sync the live TOOLS list to the Notion DB. Idempotent.

    Returns a human-readable diff report.
    """
    if not tools_list:
        return "ERROR: empty tools list passed; refusing to sync."

    db_id = _get_memory(DB_ID_CATEGORY, DB_ID_KEY)
    db_created = False
    if not db_id:
        db_id = _create_database()
        _save_memory(DB_ID_CATEGORY, DB_ID_KEY, db_id)
        db_created = True

    existing = _query_existing(db_id)

    now = datetime.now(timezone.utc).isoformat()
    live_names: set[str] = set()
    added = updated = archived = 0
    errors: list[str] = []

    for i, tool in enumerate(tools_list):
        name = tool.get("name", "")
        if not name:
            continue
        live_names.add(name)
        category = _categorize(name)
        try:
            if name in existing:
                _update_row(existing[name], tool, category, "Active", now)
                updated += 1
            else:
                _create_row(db_id, tool, category, now)
                added += 1
        except requests.HTTPError as e:
            errors.append(f"{name}: {e}")
        # Rate limit guard: stay under Notion's 3 req/sec cap
        if i % 3 == 2:
            time.sleep(1.0)

    # Archive rows that disappeared from the live list
    for name, page_id in existing.items():
        if name not in live_names:
            try:
                _archive_row(page_id, now)
                archived += 1
            except requests.HTTPError as e:
                errors.append(f"archive {name}: {e}")

    db_url = f"https://www.notion.so/{db_id.replace('-', '')}"
    bits: list[str] = []
    if db_created:
        bits.append(f"🆕 Created Tool Inventory DB under Sean's HQ.")
    bits.append(f"📊 Sync result: {len(tools_list)} live tools")
    bits.append(f"  • Added:    {added}")
    bits.append(f"  • Updated:  {updated}")
    bits.append(f"  • Archived: {archived}")
    if errors:
        bits.append(f"  • Errors:   {len(errors)} (first 3: {errors[:3]})")
    bits.append(f"\nDB: {db_url}")
    return "\n".join(bits)
