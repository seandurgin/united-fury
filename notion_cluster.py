"""Notion cluster — extracted from bot_new.py 2026-06-12.

15 functions (incl. 3 helpers) + 3 module constants + 13 SCHEMAS +
13 DISPATCH entries. Mirrors the pattern of security_recon.py and
memory_history.py — bot_new.py merges via _modular_dispatch.
"""
import json
import logging
import os
from datetime import datetime, timezone

import requests

# Logger — bot_new.py overrides this at startup so logs appear under
# the main `clawdia` logger; the local module logger is a fallback.
log = logging.getLogger("clawdia.notion")

# === Module constants (extracted from bot_new.py) ===
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}
NOTION_DS_VERSION = "2025-09-03"  # data_sources API version

# === Functions (extracted verbatim from bot_new.py) ===

def _notion_title(obj):
    if obj.get("object") == "database":
        t = obj.get("title", [])
        return t[0]["plain_text"] if t else "(untitled)"
    props = obj.get("properties", {})
    title_prop = next((v for v in props.values() if v.get("type") == "title"), None)
    if title_prop and title_prop.get("title"):
        return title_prop["title"][0].get("plain_text", "(untitled)")
    return "(untitled)"

def notion_search(query, max_results=10):
    if not NOTION_TOKEN: return "Notion not configured (missing NOTION_TOKEN)."
    try:
        r = requests.post(f"{NOTION_API}/search", headers=NOTION_HEADERS,
                          json={"query": query, "page_size": max_results}, timeout=15)
        if not r.ok: return f"Notion search error {r.status_code}: {r.text[:300]}"
        results = r.json().get("results", [])
        if not results: return f"No Notion pages match '{query}'."
        out = []
        for item in results:
            obj = item.get("object")
            title = _notion_title(item)
            out.append(f"[{obj}] {title} -- ID: {item.get('id')}")
        return f"Found {len(results)} result(s):\n" + "\n".join(out)
    except Exception as e:
        return f"Notion search failed: {e}"

def _notion_fetch_children(block_id, page_size=100):
    """Fetch children of a Notion block. Returns list of block dicts; empty on error."""
    try:
        r = requests.get(f"{NOTION_API}/blocks/{block_id}/children",
                         headers=NOTION_HEADERS, params={"page_size": page_size}, timeout=15)
        if not r.ok:
            return []
        return r.json().get("results", [])
    except Exception:
        return []

def notion_read_page(page_id):
    if not NOTION_TOKEN: return "Notion not configured (missing NOTION_TOKEN)."
    try:
        pr = requests.get(f"{NOTION_API}/pages/{page_id}", headers=NOTION_HEADERS, timeout=15)
        if not pr.ok: return f"Notion read error {pr.status_code}: {pr.text[:300]}"
        title = _notion_title(pr.json())
        blocks = _notion_fetch_children(page_id)
        lines = [f"# {title}", ""]
        budget = [500]  # max blocks to render
        _render_blocks(blocks, lines, budget)
        return "\n".join(lines)[:4000]
    except Exception as e:
        return f"Notion read failed: {e}"

def notion_append_bullet(page_id, text):
    if not NOTION_TOKEN: return "Notion not configured (missing NOTION_TOKEN)."
    try:
        payload = {"children": [{
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}
        }]}
        r = requests.patch(f"{NOTION_API}/blocks/{page_id}/children",
                           headers=NOTION_HEADERS, json=payload, timeout=15)
        if not r.ok: return f"Notion append error {r.status_code}: {r.text[:300]}"
        return f"Appended bullet to page {page_id}"
    except Exception as e:
        return f"Notion append failed: {e}"

def notion_create_page(parent_page_id, title, content=""):
    if not NOTION_TOKEN: return "Notion not configured (missing NOTION_TOKEN)."
    try:
        payload = {
            "parent": {"page_id": parent_page_id},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
        }
        if content:
            payload["children"] = [{
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": content[:2000]}}]}
            }]
        r = requests.post(f"{NOTION_API}/pages", headers=NOTION_HEADERS, json=payload, timeout=15)
        if not r.ok: return f"Notion create error {r.status_code}: {r.text[:300]}"
        pid = r.json().get("id")
        return f"Created page '{title}' (ID: {pid})"
    except Exception as e:
        return f"Notion create failed: {e}"

def notion_list_blocks(page_id, max_results=50):
    """List block IDs on a Notion page with short text previews. Use to find a block ID before editing/deleting."""
    if not NOTION_TOKEN: return "Notion not configured (missing NOTION_TOKEN)."
    try:
        r = requests.get(
            f"{NOTION_API}/blocks/{page_id}/children",
            headers=NOTION_HEADERS,
            params={"page_size": max_results},
            timeout=15
        )
        if not r.ok: return f"Notion list blocks error {r.status_code}: {r.text[:300]}"
        blocks = r.json().get("results", [])
        if not blocks: return "Page has no blocks."
        lines = []
        for b in blocks:
            bt = b.get("type")
            data = b.get(bt, {})
            rich = data.get("rich_text", [])
            text = "".join(x.get("plain_text", "") for x in rich)[:80]
            lines.append(f"[{bt}] id={b['id']} -- {text}")
        return f"Blocks on page ({len(blocks)}):\n" + "\n".join(lines)
    except Exception as e:
        return f"Notion list blocks failed: {e}"

def notion_delete_block(block_id):
    """Delete (archive) a Notion block by ID. Get the ID from notion_list_blocks or notion_read."""
    if not NOTION_TOKEN: return "Notion not configured (missing NOTION_TOKEN)."
    try:
        r = requests.delete(
            f"{NOTION_API}/blocks/{block_id}",
            headers=NOTION_HEADERS,
            timeout=15
        )
        if not r.ok: return f"Notion delete error {r.status_code}: {r.text[:300]}"
        return f"Deleted block {block_id}"
    except Exception as e:
        return f"Notion delete failed: {e}"

def notion_update_block(block_id, new_text):
    """Replace the text of a Notion block. Works for paragraph, bulleted_list_item, heading, to_do, quote."""
    if not NOTION_TOKEN: return "Notion not configured (missing NOTION_TOKEN)."
    try:
        # First fetch the block to determine its type
        g = requests.get(f"{NOTION_API}/blocks/{block_id}", headers=NOTION_HEADERS, timeout=15)
        if not g.ok: return f"Notion fetch block error {g.status_code}: {g.text[:300]}"
        block = g.json()
        bt = block.get("type")
        if bt not in ("paragraph", "bulleted_list_item", "numbered_list_item",
                      "heading_1", "heading_2", "heading_3", "to_do", "quote", "callout"):
            return f"Cannot update block of type '{bt}' (only text-bearing types supported)"
        # Build the update payload
        payload = {bt: {"rich_text": [{"type": "text", "text": {"content": new_text[:2000]}}]}}
        r = requests.patch(f"{NOTION_API}/blocks/{block_id}", headers=NOTION_HEADERS, json=payload, timeout=15)
        if not r.ok: return f"Notion update error {r.status_code}: {r.text[:300]}"
        return f"Updated block {block_id} ({bt})"
    except Exception as e:
        return f"Notion update failed: {e}"

def _notion_dedup_find(dsid, title):
    """Shape-E guard: look for an existing row in data source `dsid` whose TITLE property
    exactly equals `title` (case-insensitive, trimmed). Returns a dict
    {title, url, created} for the first match, or None if no match / on any error.
    Uses the data_sources endpoint + 2025-09-03 version because the add_* DSIDs are
    data_source IDs (the old /databases/{id}/query path 404s on them -> silent fail-open,
    which is the bug that let Shape E duplicates through). Auto-detects the title property
    so a schema rename can't silently disable the guard."""
    if not NOTION_TOKEN or not title:
        return None
    want = title.strip().lower()
    hdr = dict(NOTION_HEADERS); hdr["Notion-Version"] = NOTION_DS_VERSION
    url = f"{NOTION_API}/data_sources/{dsid}/query"
    try:
        # discover the title property name from one row (cheap, page_size=1 probe)
        probe = requests.post(url, headers=hdr, json={"page_size": 1}, timeout=15)
        if not probe.ok:
            log.warning(f"_notion_dedup_find probe {dsid} -> {probe.status_code}: {probe.text[:160]}")
            return None
        pres = probe.json().get("results", [])
        title_prop = None
        if pres:
            for pn, pv in pres[0].get("properties", {}).items():
                if pv.get("type") == "title":
                    title_prop = pn; break
        if not title_prop:
            # empty data source (no rows) => nothing to dedup against
            return None
        # exact-title filter (title type key, NOT rich_text)
        q = {"filter": {"property": title_prop, "title": {"equals": title.strip()}}, "page_size": 5}
        r = requests.post(url, headers=hdr, json=q, timeout=15)
        if not r.ok:
            log.warning(f"_notion_dedup_find query {dsid} -> {r.status_code}: {r.text[:160]}")
            return None
        for row in r.json().get("results", []):
            tp = row.get("properties", {}).get(title_prop, {}).get("title", [])
            txt = "".join(t.get("plain_text", "") for t in tp).strip()
            if txt.lower() == want:
                return {"title": txt or title,
                        "url": row.get("url", ""),
                        "created": (row.get("created_time", "") or "").split("T")[0] or "unknown"}
        return None
    except Exception as e:
        log.warning(f"_notion_dedup_find {dsid} failed (fail-open): {e}")
        return None

def notion_add_song_idea(title, stage="Spark", mood=None, hook=None, notes=None):
    """Add a row to Sean's Song Ideas database. stage: Spark/Drafting/Demo/Released/Shelved. mood: list of Heavy/Melodic/Dark/Anthemic/Introspective/Experimental."""
    if not NOTION_TOKEN: return "Notion not configured (missing NOTION_TOKEN)."
    DSID = "ea11075b-5d6f-436b-97c0-d985c426524b"
    # === Shape-E dedup guard (shared helper, data_sources endpoint) ===
    _dup = _notion_dedup_find(DSID, title)
    if _dup:
        return (f"⚠️ DUPLICATE SONG ALERT\nA song **{_dup['title']}** already exists.\n"
                f"Created: {_dup['created']}\nView: {_dup['url']}\n\n"
                f"To save anyway, change the title and try again.")
    # === end dedup guard ===
    valid_stage = {"Spark","Drafting","Demo","Released","Shelved"}
    valid_mood  = {"Heavy","Melodic","Dark","Anthemic","Introspective","Experimental"}
    if stage not in valid_stage:
        return f"ERROR: stage must be one of {sorted(valid_stage)}, got {stage!r}"
    moods = []
    if mood:
        if isinstance(mood, str):
            moods = [m.strip() for m in mood.split(",") if m.strip()]
        elif isinstance(mood, list):
            moods = [str(m).strip() for m in mood if str(m).strip()]
        bad = [m for m in moods if m not in valid_mood]
        if bad:
            return f"ERROR: mood values {bad} not in {sorted(valid_mood)}"
    
    
    props = {
        "Title": {"title": [{"type":"text","text":{"content": title[:200]}}]},
        "Stage": {"select": {"name": stage}},
    }
    if moods:
        props["Mood"] = {"multi_select": [{"name": m} for m in moods]}
    if hook:
        props["Hook"] = {"rich_text": [{"type":"text","text":{"content": hook[:1900]}}]}
    if notes:
        props["Notes"] = {"rich_text": [{"type":"text","text":{"content": notes[:1900]}}]}
    payload = {"parent": {"data_source_id": DSID}, "properties": props}
    try:
        r = requests.post(f"{NOTION_API}/pages", headers=NOTION_HEADERS, json=payload, timeout=15)
        if not r.ok: return f"Notion add_song_idea error {r.status_code}: {r.text[:300]}"
        pid = r.json().get("id","")
        bits = [f"stage={stage}"]
        if moods: bits.append(f"mood={'/'.join(moods)}")
        return f"Added song idea: {title} ({', '.join(bits)}) [ID: {pid}]"
    except Exception as e:
        return f"Notion add_song_idea failed: {e}"

def notion_raw_query_database(database_id, max_results=100):
    """Return raw Notion API JSON for a database query, or None on error.
    Used by briefing.py to render its own summary; differs from notion_query_database
    which returns a human-readable string."""
    if not NOTION_TOKEN: return None
    try:
        r = requests.post(f"{NOTION_API}/databases/{database_id}/query",
                          headers=NOTION_HEADERS, json={"page_size": max_results}, timeout=15)
        if not r.ok:
            log.warning(f"notion_raw_query_database {database_id} -> {r.status_code}: {r.text[:200]}")
            return None
        return r.json()
    except Exception as e:
        log.warning(f"notion_raw_query_database failed: {e}")
        return None

def notion_add_todo(task_name, priority="This week", category=None, due_date=None, notes=None):
    """Add a row to Sean's To-Do database. priority: Now/This week/Someday. category: Personal/Work/Family/Music/Clawdia/Truck/Home/Finance. due_date: ISO YYYY-MM-DD."""
    if not NOTION_TOKEN: return "Notion not configured (missing NOTION_TOKEN)."
    
    DSID = "2692e075-ac64-80e3-9454-000bf68150c9"
    # === Shape-E dedup guard (shared helper, data_sources endpoint) ===
    _dup = _notion_dedup_find(DSID, task_name)
    if _dup:
        return (f"⚠️ DUPLICATE TODO ALERT\nA task **{_dup['title']}** already exists.\n"
                f"Created: {_dup['created']}\nView: {_dup['url']}\n\n"
                f"To save anyway, change the title and try again.")
    # === end dedup guard ===
    valid_priority = {"Now","This week","Someday"}
    valid_category = {"Personal","Work","Family","Music","Clawdia","Truck","Home","Finance"}
    if priority not in valid_priority:
        return f"ERROR: priority must be one of {sorted(valid_priority)}, got {priority!r}"
    if category and category not in valid_category:
        return f"ERROR: category must be one of {sorted(valid_category)}, got {category!r}"
    
    
    props = {
        "Task name": {"title": [{"type":"text","text":{"content": task_name[:200]}}]},
        "Status":    {"status": {"name": "Not started"}},
        "Priority":  {"select": {"name": priority}},
    }
    if category: props["Category"] = {"select": {"name": category}}
    if due_date: props["Due date"] = {"date": {"start": due_date}}
    if notes:    props["Notes"]    = {"rich_text": [{"type":"text","text":{"content": notes[:1900]}}]}
    payload = {"parent": {"data_source_id": DSID}, "properties": props}
    try:
        r = requests.post(f"{NOTION_API}/pages", headers=NOTION_HEADERS, json=payload, timeout=15)
        if not r.ok: return f"Notion add_todo error {r.status_code}: {r.text[:300]}"
        pid = r.json().get("id","")
        bits = [f"priority={priority}"]
        if category: bits.append(f"category={category}")
        if due_date: bits.append(f"due={due_date}")
        return f"Added to-do: {task_name} ({', '.join(bits)}) [ID: {pid}]"
    except Exception as e:
        return f"Notion add_todo failed: {e}"

def notion_add_research(topic, category=None, notes=None):
    """Add a row to Sean's Research & Backlog database. category: Personal/Work/Family/Music/Clawdia/Truck/Home/Finance."""
    if not NOTION_TOKEN: return "Notion not configured (missing NOTION_TOKEN)."
    
    DSID = "0b6392cd-2285-4969-a499-0182e4eafe45"
    # === Shape-E dedup guard (shared helper, data_sources endpoint) ===
    _dup = _notion_dedup_find(DSID, topic)
    if _dup:
        return (f"⚠️ DUPLICATE RESEARCH ALERT\nA research topic **{_dup['title']}** already exists.\n"
                f"Created: {_dup['created']}\nView: {_dup['url']}\n\n"
                f"To save anyway, change the title and try again.")
    # === end dedup guard ===
    valid_category = {"Personal","Work","Family","Music","Clawdia","Truck","Home","Finance"}
    if category and category not in valid_category:
        return f"ERROR: category must be one of {sorted(valid_category)}, got {category!r}"
    
    
    props = {
        "Topic":  {"title": [{"type":"text","text":{"content": topic[:200]}}]},
        "Status": {"select": {"name": "Active"}},
    }
    if category: props["Category"] = {"select": {"name": category}}
    if notes:    props["Notes"]    = {"rich_text": [{"type":"text","text":{"content": notes[:1900]}}]}
    payload = {"parent": {"data_source_id": DSID}, "properties": props}
    try:
        r = requests.post(f"{NOTION_API}/pages", headers=NOTION_HEADERS, json=payload, timeout=15)
        if not r.ok: return f"Notion add_research error {r.status_code}: {r.text[:300]}"
        pid = r.json().get("id","")
        bits = []
        if category: bits.append(f"category={category}")
        suffix = f" ({', '.join(bits)})" if bits else ""
        return f"Added research: {topic}{suffix} [ID: {pid}]"
    except Exception as e:
        return f"Notion add_research failed: {e}"

def notion_query_database(database_id, max_results=10):
    if not NOTION_TOKEN: return "Notion not configured (missing NOTION_TOKEN)."
    try:
        r = requests.post(f"{NOTION_API}/databases/{database_id}/query",
                          headers=NOTION_HEADERS, json={"page_size": max_results}, timeout=15)
        if not r.ok: return f"Notion query error {r.status_code}: {r.text[:300]}"
        results = r.json().get("results", [])
        if not results: return "Database is empty."
        out = []
        for item in results:
            title = _notion_title(item)
            props_summary = []
            for k, v in item.get("properties", {}).items():
                t = v.get("type")
                if t == "title": continue
                if t == "select" and v.get("select"):
                    props_summary.append(f"{k}={v['select']['name']}")
                elif t == "status" and v.get("status"):
                    props_summary.append(f"{k}={v['status']['name']}")
                elif t == "checkbox":
                    props_summary.append(f"{k}={'Y' if v.get('checkbox') else 'N'}")
                elif t == "date" and v.get("date"):
                    props_summary.append(f"{k}={v['date'].get('start','')}")
            line = f"- {title}"
            if props_summary: line += f"  ({', '.join(props_summary)})"
            line += f"  [ID: {item.get('id')}]"
            out.append(line)
        return f"Found {len(results)} row(s):\n" + "\n".join(out)
    except Exception as e:
        return f"Notion query failed: {e}"

def notion_update_page_property(page_id, property_name, value, date_end=None):
    """
    Update a single property on a Notion database page.

    page_id: page ID (with or without dashes) or full Notion URL.
    property_name: human-readable property name as it appears in the database.
    value: the new value. Type interpretation is automatic based on the
           database schema fetched via Notion API.
    date_end: optional end date for date properties (ignored otherwise).

    Returns a status string. Supports these property types in v1:
      status, select, multi_select, checkbox, number, date, title, rich_text,
      url, email, phone_number.
    """
    import os as _os
    import re as _re
    import requests as _requests

    if not isinstance(page_id, str) or not page_id.strip():
        return "notion_update_page_property: page_id is required."
    if not isinstance(property_name, str) or not property_name.strip():
        return "notion_update_page_property: property_name is required."

    pid = page_id.strip()
    m = _re.search(r"([0-9a-f]{32})", pid.replace("-", "").lower())
    if not m:
        return f"notion_update_page_property: could not parse page_id from '{page_id}'."
    raw = m.group(1)
    pid_dashed = f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"

    token = _os.environ.get("NOTION_API_KEY") or _os.environ.get("NOTION_TOKEN")
    if not token:
        return "notion_update_page_property: NOTION_API_KEY not set in env."

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    try:
        r = _requests.get(
            f"https://api.notion.com/v1/pages/{pid_dashed}",
            headers=headers, timeout=15,
        )
    except Exception as e:
        return f"notion_update_page_property: page fetch failed: {e}"
    if r.status_code != 200:
        return f"notion_update_page_property: page fetch failed (HTTP {r.status_code}): {r.text[:300]}"

    page_data = r.json()
    props = page_data.get("properties", {})
    if property_name not in props:
        available = ", ".join(sorted(props.keys())[:20])
        return (f"notion_update_page_property: property '{property_name}' not found on page. "
                f"Available properties: {available}")

    prop_type = props[property_name].get("type", "unknown")

    if prop_type == "status":
        body_val = {"status": {"name": str(value)}}
    elif prop_type == "select":
        body_val = {"select": {"name": str(value)}}
    elif prop_type == "multi_select":
        if isinstance(value, list):
            names = [str(v).strip() for v in value if str(v).strip()]
        else:
            names = [s.strip() for s in str(value).split(",") if s.strip()]
        body_val = {"multi_select": [{"name": n} for n in names]}
    elif prop_type == "checkbox":
        if isinstance(value, bool):
            v = value
        else:
            v = str(value).strip().lower() in ("true", "yes", "1", "checked", "done")
        body_val = {"checkbox": v}
    elif prop_type == "number":
        try:
            v = float(value)
            if v.is_integer():
                v = int(v)
        except (TypeError, ValueError):
            return f"notion_update_page_property: '{value}' is not a valid number for property '{property_name}'."
        body_val = {"number": v}
    elif prop_type == "date":
        date_payload = {"start": str(value)}
        if date_end:
            date_payload["end"] = str(date_end)
        body_val = {"date": date_payload}
    elif prop_type == "title":
        body_val = {"title": [{"type": "text", "text": {"content": str(value)}}]}
    elif prop_type == "rich_text":
        body_val = {"rich_text": [{"type": "text", "text": {"content": str(value)}}]}
    elif prop_type == "url":
        body_val = {"url": str(value)}
    elif prop_type == "email":
        body_val = {"email": str(value)}
    elif prop_type == "phone_number":
        body_val = {"phone_number": str(value)}
    else:
        return (f"notion_update_page_property: property '{property_name}' has type '{prop_type}' "
                f"which is not supported in v1. Supported types: status, select, multi_select, "
                f"checkbox, number, date, title, rich_text, url, email, phone_number.")

    body = {"properties": {property_name: body_val}}
    try:
        r = _requests.patch(
            f"https://api.notion.com/v1/pages/{pid_dashed}",
            headers=headers, json=body, timeout=15,
        )
    except Exception as e:
        return f"notion_update_page_property: PATCH failed: {e}"

    if r.status_code == 200:
        return f"Updated '{property_name}' on page {pid_dashed[:8]}... (type={prop_type})."
    return f"notion_update_page_property: PATCH failed (HTTP {r.status_code}): {r.text[:400]}"

def notion_archive_page(page_id):
    """Archive a Notion page (reversible — recoverable from Notion trash for 30 days).
    Use for 'delete that task', 'remove this entry', 'archive that page'.
    Returns confirmation or error string."""
    import os, requests
    token = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_KEY")
    if not token:
        return "ERROR: NOTION_TOKEN not in env."
    page_id = (page_id or "").strip()
    if not page_id:
        return "ERROR: page_id is required."
    try:
        r = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            json={"archived": True},
            timeout=15,
        )
        if r.status_code == 200:
            return f"Archived Notion page {page_id[:8]}... (recoverable from trash for 30 days)."
        if r.status_code == 404:
            return f"ERROR: Notion page {page_id[:8]}... not found or not shared with the Clawdia integration."
        return f"ERROR: Notion API returned HTTP {r.status_code}: {r.text[:200]}"
    except requests.exceptions.Timeout:
        return "ERROR: Notion API timed out after 15s. The archive may still have applied — verify by re-fetching the page."
    except Exception as e:
        return f"ERROR: notion_archive_page failed: {type(e).__name__}: {e}"

# === SCHEMAS (extracted from TOOLS list) ===
SCHEMAS = [
    {"name":"notion_search","description":"Search live Notion DBs and Sean-facing pages by title or content. Returns a list with IDs. USE FOR: Sean's To-Do DB, Research DB, Song Ideas DB, Sean's HQ pages, family-visible content. DO NOT USE FOR: backlog, architecture, conventions, archive — those live in /opt/clawdia/docs/ and are searched via docs_search (sub-second, no API timeout). If Sean asks about a backlog item / what shipped / past session notes / working conventions — use docs_search FIRST.","input_schema":{"type":"object","properties":{"query":{"type":"string"},"max_results":{"type":"integer","default":10}},"required":["query"]}},
    {"name":"notion_read","description":"Read a live Notion page by ID and return its content. USE FOR: Sean-facing DB rows, Song Ideas pages, To-Do entries, family pages. DO NOT USE FOR: backlog/architecture/conventions/archive — those moved to /opt/clawdia/docs/. To read a migrated doc, use docs_read('backlog.md'), docs_read('architecture.md'), docs_read('conventions.md'), or docs_read('archive/<name>.md').","input_schema":{"type":"object","properties":{"page_id":{"type":"string"}},"required":["page_id"]}},
    {"name":"notion_append_bullet","description":"Append a bullet-point item to a live Notion page. DO NOT use for the Enhancement Backlog — that moved to /opt/clawdia/docs/backlog.md on 2026-05-16; use docs_append('backlog.md', content) instead. This tool is for ad-hoc appending to Sean-facing Notion pages only.","input_schema":{"type":"object","properties":{"page_id":{"type":"string"},"text":{"type":"string"}},"required":["page_id","text"]}},
    {"name":"notion_create_page","description":"Create a new Notion page under a parent page.","input_schema":{"type":"object","properties":{"parent_page_id":{"type":"string"},"title":{"type":"string"},"content":{"type":"string"}},"required":["parent_page_id","title"]}},
    {"name":"notion_list_blocks","description":"List block IDs on a live Notion page with short text previews. Use this to find the block ID before calling notion_update_block or notion_delete_block on a Sean-facing page. NOT applicable to migrated Claude docs — those use docs_edit(file, old_str, new_str) instead, which does surgical str_replace without needing block IDs.","input_schema":{"type":"object","properties":{"page_id":{"type":"string"},"max_results":{"type":"integer","default":50}},"required":["page_id"]}},
    {"name":"notion_delete_block","description":"Delete a Notion block by ID. Use to remove items from a page. Get the block ID from notion_list_blocks first. Action is reversible in the Notion UI (block is archived, not hard-deleted).","input_schema":{"type":"object","properties":{"block_id":{"type":"string"}},"required":["block_id"]}},
    {"name":"notion_update_block","description":"Replace the text of a Notion block. Works for paragraphs, bullets, headings, to-dos, and quotes. Get the block ID from notion_list_blocks first.","input_schema":{"type":"object","properties":{"block_id":{"type":"string"},"new_text":{"type":"string"}},"required":["block_id","new_text"]}},
    {"name":"notion_query_database","description":"Query a Notion database and list its rows with properties.","input_schema":{"type":"object","properties":{"database_id":{"type":"string"},"max_results":{"type":"integer","default":10}},"required":["database_id"]}},
    {"name":"notion_add_todo","description":"Add a row to Sean's To-Do database (canonical task list under 'Sean's HQ'). Use when Sean says 'add to my to-do list', 'remind me to X', etc. Status is auto-set to Not started. Default priority is 'This week'.","input_schema":{"type":"object","properties":{"task_name":{"type":"string"},"priority":{"type":"string","enum":["Now","This week","Someday"],"default":"This week"},"category":{"type":"string","enum":["Personal","Work","Family","Music","Clawdia","Truck","Home","Finance"]},"due_date":{"type":"string","description":"ISO date YYYY-MM-DD"},"notes":{"type":"string"}},"required":["task_name"]}},
    {"name":"notion_add_research","description":"Add a row to Sean's Research & Backlog database (canonical research/investigate list). Use when Sean says 'add to research', 'thing to look into', 'something to decide on later'. Status is auto-set to Active.","input_schema":{"type":"object","properties":{"topic":{"type":"string"},"category":{"type":"string","enum":["Personal","Work","Family","Music","Clawdia","Truck","Home","Finance"]},"notes":{"type":"string"}},"required":["topic"]}},
    {"name":"notion_add_song_idea","description":"Add a row to Sean's Song Ideas database (Hollowed Ground songwriting capture). Use when Sean says 'song idea', 'capture this lyric', 'add to song ideas', etc. Stage auto-defaults to 'Spark'. Mood is a list — pass an array or comma-separated string of any of: Heavy, Melodic, Dark, Anthemic, Introspective, Experimental.","input_schema":{"type":"object","properties":{"title":{"type":"string"},"stage":{"type":"string","enum":["Spark","Drafting","Demo","Released","Shelved"],"default":"Spark"},"mood":{"type":"array","items":{"type":"string","enum":["Heavy","Melodic","Dark","Anthemic","Introspective","Experimental"]}},"hook":{"type":"string","description":"the hook/chorus line or main lyrical idea"},"notes":{"type":"string"}},"required":["title"]}},
    {"name":"notion_update_page_property","description":"Update a single property on a Notion database row (page). Use this to flip Status, change Priority, set Due Date, check/uncheck a checkbox, etc. on a database page - the existing notion_update_block tool only edits page CONTENT, not the property fields shown as columns in the database view. Auto-detects the property type from the database schema; supports status, select, multi_select, checkbox, number, date, title, rich_text, url, email, phone_number. For unsupported property types returns a clear error naming the actual type. ALWAYS use notion_read on the page first to confirm the property name and current value before updating destructively.","input_schema":{"type":"object","properties":{"page_id":{"type":"string","description":"Notion page ID (with or without dashes) or full Notion URL."},"property_name":{"type":"string","description":"Exact property name as shown in the database (case-sensitive)."},"value":{"type":"string","description":"New value. For status/select: option name. For checkbox: true/false or yes/no. For number: numeric string. For date: ISO date (YYYY-MM-DD) or datetime. For multi_select: comma-separated names. For title/rich_text/url/email/phone_number: literal value."},"date_end":{"type":"string","description":"Optional end date for date-range properties (ISO format). Ignored for non-date properties."}},"required":["page_id","property_name","value"]}},
    {"name":"notion_archive_page","description":"Archive (delete) a Notion page or database row by id. RECOVERABLE — page goes to Notion trash for 30 days, can be restored manually. Use for 'delete that task', 'remove this todo', 'archive that page', 'get rid of that entry'. CONFIRMATION GATE: before calling, surface the page title (from prior notion_search/notion_read) to Sean and wait for explicit yes/confirm before archiving. Returns confirmation string with the archived page id.","input_schema":{"type":"object","properties":{"page_id":{"type":"string","description":"Notion page ID (with or without dashes)."}},"required":["page_id"]}},
]

# === DISPATCH wrappers ===
# Each wrapper is a SYNC function (called via asyncio.to_thread from
# bot_new.py run_tool merged dispatch). The original dispatch elif
# blocks used `await asyncio.to_thread(fn, ...)` — that wrapper is now
# at the run_tool level, so these wrappers just call fn() directly.

def _dispatch_notion_search(inputs):
    _q = inputs.get("query","").strip()
    if not _q: return "ERROR: notion_search requires query."
    return notion_search(_q, inputs.get("max_results",10))

def _dispatch_notion_read(inputs):
    _pid = inputs.get("page_id","").strip()
    if not _pid: return "ERROR: notion_read requires page_id."
    return notion_read_page(_pid)

def _dispatch_notion_append_bullet(inputs):
    _pid = inputs.get("page_id","").strip()
    _txt = inputs.get("text","")
    if not _pid: return "ERROR: notion_append_bullet requires page_id (Notion page UUID)."
    if not _txt: return "ERROR: notion_append_bullet requires text (the bullet content)."
    return notion_append_bullet(_pid, _txt)

def _dispatch_notion_create_page(inputs):
    _ppid = inputs.get("parent_page_id","").strip()
    _t = inputs.get("title","").strip()
    if not _ppid or not _t:
        return "ERROR: notion_create_page requires parent_page_id and title."
    return notion_create_page(_ppid, _t, inputs.get("content",""))

def _dispatch_notion_list_blocks(inputs):
    _pid = inputs.get("page_id","").strip()
    if not _pid: return "ERROR: notion_list_blocks requires page_id."
    return notion_list_blocks(_pid, inputs.get("max_results",50))

def _dispatch_notion_delete_block(inputs):
    _bid = inputs.get("block_id","").strip()
    if not _bid: return "ERROR: notion_delete_block requires block_id."
    return notion_delete_block(_bid)

def _dispatch_notion_update_block(inputs):
    _bid = inputs.get("block_id","").strip()
    _nt = inputs.get("new_text","")
    if not _bid: return "ERROR: notion_update_block requires block_id."
    if not _nt: return "ERROR: notion_update_block requires new_text."
    return notion_update_block(_bid, _nt)

def _dispatch_notion_query_database(inputs):
    _did = inputs.get("database_id","").strip()
    if not _did: return "ERROR: notion_query_database requires database_id."
    return notion_query_database(_did, inputs.get("max_results",10))

def _dispatch_notion_add_todo(inputs):
    _tn = inputs.get("task_name","").strip()
    if not _tn: return "ERROR: notion_add_todo requires task_name."
    return notion_add_todo(_tn, inputs.get("priority","This week"), inputs.get("category") or None, inputs.get("due_date") or None, inputs.get("notes") or None)

def _dispatch_notion_add_research(inputs):
    _tp = inputs.get("topic","").strip()
    if not _tp: return "ERROR: notion_add_research requires topic."
    return notion_add_research(_tp, inputs.get("category") or None, inputs.get("notes") or None)

def _dispatch_notion_add_song_idea(inputs):
    _tt = inputs.get("title","").strip()
    if not _tt: return "ERROR: notion_add_song_idea requires title."
    return notion_add_song_idea(_tt, inputs.get("stage","Spark"), inputs.get("mood") or None, inputs.get("hook") or None, inputs.get("notes") or None)

def _dispatch_notion_update_page_property(inputs):
    _pid = (inputs.get("page_id") or "").strip()
    _pn = (inputs.get("property_name") or "").strip()
    _v = inputs.get("value", "")
    if not _pid: return "ERROR: notion_update_page_property requires page_id."
    if not _pn: return "ERROR: notion_update_page_property requires property_name."
    return notion_update_page_property(_pid, _pn, _v, inputs.get("date_end"))

def _dispatch_notion_archive_page(inputs):
    _page_id = inputs.get("page_id") if isinstance(inputs, dict) else None
    if not _page_id:
        return "ERROR: notion_archive_page requires page_id."
    return notion_archive_page(_page_id)

DISPATCH = {
    "notion_search": _dispatch_notion_search,
    "notion_read": _dispatch_notion_read,
    "notion_append_bullet": _dispatch_notion_append_bullet,
    "notion_create_page": _dispatch_notion_create_page,
    "notion_list_blocks": _dispatch_notion_list_blocks,
    "notion_delete_block": _dispatch_notion_delete_block,
    "notion_update_block": _dispatch_notion_update_block,
    "notion_query_database": _dispatch_notion_query_database,
    "notion_add_todo": _dispatch_notion_add_todo,
    "notion_add_research": _dispatch_notion_add_research,
    "notion_add_song_idea": _dispatch_notion_add_song_idea,
    "notion_update_page_property": _dispatch_notion_update_page_property,
    "notion_archive_page": _dispatch_notion_archive_page,
}
