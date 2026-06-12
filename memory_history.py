"""
Memory, history search, and backlog tools.

Extracted from bot_new.py 2026-06-11 as part of the modularization effort.
Pure SQLite/filesystem operations against /var/lib/clawdia/memory.db and
/opt/clawdia/docs/backlog.md. No shared state with bot_new beyond those paths.
"""
import os, re, sqlite3, json, time
from datetime import datetime, timezone, timedelta


def get_conn(): return sqlite3.connect(DB_PATH)


# ---- Module-level constants ----

DB_PATH           = os.environ.get("DB_PATH", "/var/lib/clawdia/memory.db")


# ---- Function implementations ----

def memory_save(category, key, value):
    # Returns (actual_category, actual_key, action) so callers can narrate the TRUE
    # landing spot (Shape D fix). action in {"updated","redirected","inserted"}.
    # Returns None only when args are invalid.
    if not category or not key or not value: return None
    now = datetime.now(timezone.utc).isoformat()
    cat_s = str(category).strip()
    key_s = str(key).strip()
    val_s = str(value).strip()

    # Dedup: if the same category already contains a row with substantially
    # the same value, update that existing row instead of creating a new key.
    # "Substantially the same" = first 200 chars match case-insensitively
    # after whitespace normalization.
    import re as _re_dedup
    def _norm(s): return _re_dedup.sub(r"\s+", " ", s.lower()).strip()
    val_norm_full = _norm(val_s)
    val_norm = val_norm_full[:200]

    # Extract identifier-like tokens for cross-category dedup.
    # An "identifier" = alphanumeric token >=6 chars containing at least one digit.
    # Captures: member numbers (154113886), cert IDs (35BHJ48, COMP001020670383),
    # account numbers (3530457), EINs (33-3900987), file IDs (1CtnKqwu...), etc.
    # Excludes: pure-letter words (certificate, microsoft, member, etc.) so
    # cross-cat fires only on actual identifier match, not generic vocabulary.
    def _identifiers(text):
        tokens = _re_dedup.findall(r"[a-z0-9]+", text)
        return {t for t in tokens if len(t) >= 6 and any(c.isdigit() for c in t)}

    val_ids = _identifiers(val_norm_full)

    # Substring-containment threshold (only gates signal (a), not signal (b)).
    # An identifier match alone is high-cardinality enough to dedupe even when
    # the value is short.
    MIN_CONTAIN_LEN = 25

    with get_conn() as conn:
        if val_norm:
            # Pass 1: same-category substantially-similar-value dedup (original behavior).
            existing = conn.execute(
                "SELECT key, value FROM memory WHERE category=?", (cat_s,)
            ).fetchall()
            for ex_key, ex_val in existing:
                ex_norm = _norm(ex_val or "")[:200]
                if ex_norm and ex_norm == val_norm:
                    conn.execute(
                        "UPDATE memory SET value=?, updated=? WHERE category=? AND key=?",
                        (val_s, now, cat_s, ex_key)
                    )
                    return (cat_s, ex_key, "updated")

            # Pass 2: cross-category key-drift guard. If THIS key already exists
            # in ANY OTHER category, check two signals for "same fact":
            #
            # (a) Substring containment after normalization. Gated by length to
            #     avoid false positives on short generic values ("ok", "yes").
            #     Catches the case where one value is an appended/prepended
            #     version of the other (e.g. "X" vs "X plus more details").
            #
            # (b) Shared identifier token (digit-containing alphanumeric, 6+ chars).
            #     NOT gated by length — identifiers are high-entropy enough that
            #     a shared identifier across same-key-different-category rows is
            #     a reliable "same fact" signal regardless of value length.
            #     Catches the paraphrase case where the same identifier appears
            #     in both values but the surrounding prose has diverged
            #     (e.g. "Member #154113886, 58,285 points" vs "Member #154113886
            #     - 58,285 points as of May 2026"), AND the short case where
            #     the value is brief but identifier-bearing
            #     (e.g. "Member #35BHJ48, 0 miles" — only 24 normalized chars).
            #
            # Conservative: requires same KEY AND (substring OR shared identifier),
            # not just one of these alone. Same key + different identifier suggests
            # a true category split (e.g. Sean's clearance vs Heather's clearance
            # — different IDs, both under "clearance"). Same identifier in unrelated
            # keys won't fire because the key match gate excludes them.
            cross_cat = conn.execute(
                "SELECT category, value FROM memory WHERE key=? AND category!=?",
                (key_s, cat_s)
            ).fetchall()
            for ex_cat, ex_val in cross_cat:
                ex_norm_full = _norm(ex_val or "")
                if not ex_norm_full:
                    continue

                # Signal (a): substring containment — gated by length to avoid
                # false positives on short generic values.
                contained = False
                if len(ex_norm_full) >= MIN_CONTAIN_LEN and len(val_norm_full) >= MIN_CONTAIN_LEN:
                    contained = (val_norm_full in ex_norm_full or ex_norm_full in val_norm_full)

                # Signal (b): shared identifier token — not length-gated.
                ex_ids = _identifiers(ex_norm_full)
                shared_ids = val_ids & ex_ids

                if contained or shared_ids:
                    conn.execute(
                        "UPDATE memory SET value=?, updated=? WHERE category=? AND key=?",
                        (val_s, now, ex_cat, key_s)
                    )
                    return (ex_cat, key_s, "redirected")

        conn.execute("INSERT INTO memory(category,key,value,created,updated) VALUES(?,?,?,?,?) ON CONFLICT(category,key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
            (cat_s, key_s, val_s, now, now))
        return (cat_s, key_s, "inserted")

def memory_delete(category, key):
    with get_conn() as conn:
        return conn.execute("DELETE FROM memory WHERE category=? AND key=?", (category, key)).rowcount > 0

def _recall_recent_impl(query, hours=72):
    """Search the history table for past Telegram exchanges containing query.

    Returns matching exchanges with timestamps, role (user/assistant), and
    a content snippet. Substring match, case-insensitive, no regex.

    Caps: max 20 results, max 168 hours (7 days) lookback.

    Use when Sean references something he said or you generated earlier
    that's no longer in your active context window. The rolling history
    is YOUR limitation, not Sean's mistake -- check before insisting it
    doesn't exist.
    """
    try:
        import sqlite3, os
        from datetime import datetime, timezone, timedelta, timedelta
        if not query or not query.strip():
            return 'ERROR: recall_recent requires a non-empty query string.'
        try:
            hours = int(hours)
        except (TypeError, ValueError):
            hours = 72
        hours = max(1, min(168, hours))

        db_path = os.environ.get('DB_PATH', '/var/lib/clawdia/memory.db')
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        q_lower = '%' + query.lower() + '%'

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            'SELECT ts, role, content FROM history '
            'WHERE ts >= ? AND LOWER(content) LIKE ? '
            'ORDER BY id DESC LIMIT 20',
            (cutoff, q_lower)
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return f'No exchanges in the last {hours}h matched {query!r}. Tried substring match (case-insensitive) on conversation history. If Sean is sure this happened, it may be older than {hours}h or in a separate Telegram conversation.'

        lines = [f'Found {len(rows)} match(es) in last {hours}h for {query!r}:', '']
        for r in rows:
            ts = r['ts']
            role = r['role']
            content = r['content']
            # Truncate very long content for readability
            if len(content) > 400:
                # Show context around the match if possible
                lc = content.lower()
                idx = lc.find(query.lower())
                if idx >= 0:
                    start = max(0, idx - 100)
                    end = min(len(content), idx + len(query) + 250)
                    snippet = ('...' if start > 0 else '') + content[start:end] + ('...' if end < len(content) else '')
                else:
                    snippet = content[:400] + '...'
            else:
                snippet = content
            lines.append(f'[{ts}] [{role}] {snippet}')
            lines.append('')
        return '\n'.join(lines)
    except Exception as e:
        return f'recall_recent error: {e}'

def _memory_search_impl(query, category=None, limit=20):
    """Substring search across memory.key + memory.value, case-insensitive.

    query: required, non-empty
    category: optional, restrict to single category
    limit: max results (default 20, hard cap 50)

    Returns formatted text, sorted by updated DESC.
    """
    try:
        if not query or not str(query).strip():
            return "ERROR: memory_search requires a non-empty query."
        q = str(query).strip()
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(50, limit))

        like_pattern = "%" + q + "%"
        with get_conn() as conn:
            if category and str(category).strip():
                cur = conn.execute(
                    "SELECT category, key, value, updated FROM memory "
                    "WHERE category = ? AND (key LIKE ? COLLATE NOCASE OR value LIKE ? COLLATE NOCASE) "
                    "ORDER BY updated DESC LIMIT ?",
                    (str(category).strip(), like_pattern, like_pattern, limit)
                )
            else:
                cur = conn.execute(
                    "SELECT category, key, value, updated FROM memory "
                    "WHERE key LIKE ? COLLATE NOCASE OR value LIKE ? COLLATE NOCASE "
                    "ORDER BY updated DESC LIMIT ?",
                    (like_pattern, like_pattern, limit)
                )
            rows = cur.fetchall()

        if not rows:
            scope = f" in category '{category}'" if category else ""
            return f"No memory entries matching '{q}'{scope}."

        lines = [f"Found {len(rows)} memory entries matching '{q}':"]
        for cat, key, value, updated in rows:
            updated_short = (updated or "")[:10]  # YYYY-MM-DD
            val_preview = (value or "")[:200]
            if value and len(value) > 200:
                val_preview += "..."
            lines.append(f"  [{cat}/{key}] {val_preview} (updated {updated_short})")
        return "\n".join(lines)
    except Exception as e:
        return f"memory_search error: {e}"

def memory_load_all(core_only=False):
    with get_conn() as conn:
        if core_only:
            rows = conn.execute("SELECT category,key,value,updated FROM memory WHERE COALESCE(tier,'core')='core' ORDER BY category,key").fetchall()
        else:
            rows = conn.execute("SELECT category,key,value,updated FROM memory ORDER BY category,key").fetchall()
    if not rows: return "(no memories stored yet)"
    lines=[]; cur_cat=None
    for cat,key,val,updated in rows:
        if cat!=cur_cat: lines.append(f"\n[{cat.upper()}]"); cur_cat=cat
        lines.append(f"  {key}: {val}  (updated {updated[:10]})")
    return "\n".join(lines).strip()

def backlog_add(text):
    """Append a one-line entry to the Inbox section of the Enhancement Backlog.
    The backlog now lives at /opt/clawdia/docs/backlog.md (migrated off Notion
    2026-05-16). This writes there, newest-first, under the '# Inbox' heading.
    Reserved for Clawdia's own capability gaps surfaced during conversation.
    Sean's own captures (ideas/research/personal todos) route to notion_add_research.
    Returns confirmation or error string."""
    import os
    from datetime import datetime, timezone, timedelta
    text = (text or "").strip()
    if not text:
        return "ERROR: text is required."
    DOCS_DIR = "/opt/clawdia/docs"
    path = os.path.join(DOCS_DIR, "backlog.md")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bullet = f"- {ts} \u2014 {text}"
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return f"ERROR: backlog_add could not read backlog.md: {e}"
    # Inbox heading uses the inbox emoji; match it tolerantly.
    import re as _re
    m = _re.search(r"(^#+\s*\U0001f4e5?\s*Inbox\s*\n\n)", content, _re.MULTILINE)
    if not m:
        # Fallback: match a heading line containing the word Inbox
        m = _re.search(r"(^#+[^\n]*Inbox[^\n]*\n\n)", content, _re.MULTILINE)
    if not m:
        return "ERROR: backlog_add could not find the '# Inbox' heading in backlog.md."
    insert_at = m.end()
    new_content = content[:insert_at] + bullet + "\n" + content[insert_at:]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        return f"ERROR: backlog_add could not write backlog.md: {e}"
    return f"Added to backlog Inbox: {ts} \u2014 {text}"


# ---- Shape-E review-based dedup (Path C — backlog 2026-05-20) ----
# Surfaces same-category paraphrase-drift candidates for HUMAN review.
# Path B (algorithmic auto-merge via containment) is PROVEN UNSAFE on real
# data — see backlog entry 2026-05-20. DO NOT auto-merge from this detector.

_SHAPE_E_STOPWORDS = {
    "the","a","an","and","or","but","is","are","was","were","be","been","being",
    "to","of","in","on","at","by","for","with","from","as","that","this","these",
    "those","it","its","he","she","they","them","his","her","their","i","my","me",
    "you","your","yours","we","our","ours","sean","heather","durgin","has","have",
    "had","do","does","did","will","would","should","could","may","might","can",
    "if","then","than","also","just","not","no","yes","so","too","very","much",
    "really","quite","some","any","all","each","every","one","two",
}

def _shape_e_tokenize(s):
    import re as _re_se
    toks = _re_se.findall(r"[a-z0-9]+", (s or "").lower())
    return [t for t in toks if t not in _SHAPE_E_STOPWORDS and len(t) >= 2]

def _shape_e_detect(min_score=0.7, max_pairs=15, category=None, min_tokens_each=3):
    """Find same-category Shape-E candidate pairs. DOES NOT MUTATE THE DB.
    Human review only — never auto-merge."""
    from difflib import SequenceMatcher
    with get_conn() as conn:
        q = "SELECT id, category, key, value FROM memory"
        params = ()
        if category:
            q += " WHERE category = ?"
            params = (category,)
        rows = conn.execute(q, params).fetchall()
    by_cat = {}
    for rid, cat, key, val in rows:
        by_cat.setdefault(cat, []).append((rid, key, val))
    token_cache = {rid: set(_shape_e_tokenize(val)) for rid, _, _, val in rows}
    candidates = []
    for cat, items in by_cat.items():
        n = len(items)
        if n < 2:
            continue
        for i in range(n):
            for j in range(i+1, n):
                rid_a, key_a, val_a = items[i]
                rid_b, key_b, val_b = items[j]
                toks_a = token_cache[rid_a]
                toks_b = token_cache[rid_b]
                if len(toks_a) < min_tokens_each or len(toks_b) < min_tokens_each:
                    continue
                overlap = toks_a & toks_b
                if not overlap:
                    continue
                coverage = len(overlap) / min(len(toks_a), len(toks_b))
                sm = SequenceMatcher(None, (val_a or '').lower(), (val_b or '').lower())
                qr = sm.quick_ratio()
                if qr < min_score and coverage < min_score:
                    continue
                seq = sm.ratio() if qr >= min_score else 0.0
                score = max(coverage, seq)
                if score < min_score:
                    continue
                if len(val_a) > len(val_b):
                    k_id, k_key, k_val, d_id, d_key, d_val = rid_a, key_a, val_a, rid_b, key_b, val_b
                elif len(val_b) > len(val_a):
                    k_id, k_key, k_val, d_id, d_key, d_val = rid_b, key_b, val_b, rid_a, key_a, val_a
                else:
                    if rid_a < rid_b:
                        k_id, k_key, k_val, d_id, d_key, d_val = rid_a, key_a, val_a, rid_b, key_b, val_b
                    else:
                        k_id, k_key, k_val, d_id, d_key, d_val = rid_b, key_b, val_b, rid_a, key_a, val_a
                basis = "containment" if coverage >= seq else "sequence"
                candidates.append({
                    "category": cat,
                    "keep_id": k_id, "keep_key": k_key, "keep_value": k_val,
                    "drop_id": d_id, "drop_key": d_key, "drop_value": d_val,
                    "score": round(score, 3),
                    "basis": basis,
                    "token_coverage": round(coverage, 3),
                    "sequence_ratio": round(seq, 3),
                })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:max_pairs]

def _shape_e_merge_impl(keep_id, drop_id, merged_value=None):
    """Merge two memory rows after human review. Deletes drop_id; optionally
    updates keep_id with merged_value. Intra-category only. Logs SHAPE_E_MERGE."""
    from datetime import datetime as _dt, timezone as _tz
    import logging as _lg_se
    now = _dt.now(_tz.utc).isoformat()
    with get_conn() as conn:
        keep_row = conn.execute(
            "SELECT id, category, key, value FROM memory WHERE id = ?", (keep_id,)
        ).fetchone()
        drop_row = conn.execute(
            "SELECT id, category, key, value FROM memory WHERE id = ?", (drop_id,)
        ).fetchone()
        if not keep_row:
            return f"ERROR: keep_id={keep_id} not found."
        if not drop_row:
            return f"ERROR: drop_id={drop_id} not found."
        if keep_row[1] != drop_row[1]:
            return (f"ERROR: keep and drop are in different categories "
                    f"({keep_row[1]!r} vs {drop_row[1]!r}). Shape-E merge is "
                    f"intra-category only.")
        snapshot = {
            "merged_at": now,
            "kept": {"id": keep_row[0], "category": keep_row[1], "key": keep_row[2], "value_was": keep_row[3]},
            "dropped": {"id": drop_row[0], "category": drop_row[1], "key": drop_row[2], "value_was": drop_row[3]},
            "merged_value_provided": bool(merged_value),
        }
        if merged_value is not None and str(merged_value).strip():
            new_val = str(merged_value).strip()
            conn.execute(
                "UPDATE memory SET value = ?, updated = ? WHERE id = ?",
                (new_val, now, keep_id)
            )
            snapshot["kept"]["value_now"] = new_val
        else:
            snapshot["kept"]["value_now"] = keep_row[3]
        conn.execute("DELETE FROM memory WHERE id = ?", (drop_id,))
        conn.commit()
    _lg_se.getLogger(__name__).warning(f"SHAPE_E_MERGE: {snapshot}")
    kept_v = snapshot["kept"]["value_now"]
    drop_v = snapshot["dropped"]["value_was"]
    return (
        f"Merged. KEPT [{snapshot['kept']['category']}/{snapshot['kept']['key']}] (id={snapshot['kept']['id']})\n"
        f"  value: {kept_v[:200]}{'...' if len(kept_v)>200 else ''}\n"
        f"DELETED [{snapshot['dropped']['category']}/{snapshot['dropped']['key']}] (id={snapshot['dropped']['id']})\n"
        f"  was: {drop_v[:200]}{'...' if len(drop_v)>200 else ''}\n"
        f"Logged to journal as SHAPE_E_MERGE for audit trail."
    )

def _dispatch_memory_dedup_scan(inputs):
    min_score = inputs.get("min_score", 0.7)
    try:
        min_score = float(min_score)
    except (TypeError, ValueError):
        return f"ERROR: min_score must be numeric (got {min_score!r})."
    if not (0.0 < min_score < 1.0):
        return f"ERROR: min_score must be between 0 and 1 (got {min_score})."
    category = inputs.get("category") or None
    if category:
        category = str(category).strip() or None
    try:
        limit = int(inputs.get("limit", 15))
    except (TypeError, ValueError):
        return "ERROR: limit must be an integer."
    limit = max(1, min(limit, 50))
    candidates = _shape_e_detect(min_score=min_score, max_pairs=limit, category=category)
    if not candidates:
        scope = f"category={category}" if category else "all categories"
        return f"No Shape-E candidates found (scope: {scope}, min_score={min_score})."
    lines = [f"Found {len(candidates)} Shape-E candidate pair(s) (scope: {category or 'all'}, min_score={min_score}). Review and call memory_dedup_merge to consolidate any pair you confirm:"]
    for i, c in enumerate(candidates, 1):
        lines.append("")
        lines.append(f"[{i}] [{c['category']}] score={c['score']} ({c['basis']}: cov={c['token_coverage']}, seq={c['sequence_ratio']})")
        kv = c['keep_value']
        dv = c['drop_value']
        lines.append(f"  KEEP id={c['keep_id']} key={c['keep_key']!r}")
        lines.append(f"       value: {kv[:200]}{'...' if len(kv)>200 else ''}")
        lines.append(f"  DROP id={c['drop_id']} key={c['drop_key']!r}")
        lines.append(f"       value: {dv[:200]}{'...' if len(dv)>200 else ''}")
    return "\n".join(lines)

def _dispatch_memory_dedup_merge(inputs):
    keep_id = inputs.get("keep_id")
    drop_id = inputs.get("drop_id")
    merged_value = inputs.get("merged_value")
    if keep_id is None or drop_id is None:
        return "ERROR: memory_dedup_merge requires keep_id and drop_id."
    try:
        keep_id = int(keep_id)
        drop_id = int(drop_id)
    except (TypeError, ValueError):
        return f"ERROR: keep_id and drop_id must be integers (got {keep_id!r} and {drop_id!r})."
    if keep_id == drop_id:
        return "ERROR: keep_id and drop_id must be different rows."
    return _shape_e_merge_impl(keep_id, drop_id, merged_value)


# ---- Tool schemas ----
SCHEMAS = [
    {"name":"memory_dedup_scan","description":"Scan the memory store for Shape-E candidates: pairs of rows in the SAME category where one value's content is largely contained in the other, suggesting paraphrase drift of the same fact across different keys (e.g. 'Sean likes Star Trek' vs 'Sean is a Star Trek fan particularly likes LCARS aesthetic'). Surfaces candidates for HUMAN REVIEW \u2014 does NOT mutate the database. Use when Sean asks to find duplicate memories, scan for dupes, show Shape-E candidates, or proactively when you suspect you saved the same fact twice under different keys. Returns up to limit pairs sorted by similarity score descending. For each pair: which row to KEEP (longer value, more context) and which to DROP, plus the score and basis (token containment or sequence similarity). Sean reviews and explicitly calls memory_dedup_merge for any pair he confirms. Optional category filter scopes scan to one category like 'preferences' or 'work'.","input_schema":{"type":"object","properties":{"min_score":{"type":"number","default":0.7,"description":"Similarity threshold 0.0-1.0. Higher = stricter. 0.7 is balanced default; 0.6 surfaces weaker matches including some false positives; 0.85+ only near-identical."},"category":{"type":"string","description":"Optional. Restrict scan to one memory category. Empty/omitted = scan all categories."},"limit":{"type":"integer","default":15,"description":"Max candidate pairs to return (1-50)."}}}},
    {"name":"memory_dedup_merge","description":"Merge two memory rows after human review. Deletes the DROP row and optionally updates the KEEP row with a consolidated value. Use ONLY after Sean explicitly approves a specific pair from a memory_dedup_scan result \u2014 never autonomously, never as a batch. Intra-category only: refuses to merge rows from different categories. The operation logs SHAPE_E_MERGE to the journal for an audit trail. Reversibility note: the dropped row is DELETED (not soft-deleted) \u2014 Sean must be sure before calling.","input_schema":{"type":"object","properties":{"keep_id":{"type":"integer","description":"Row ID to KEEP. From memory_dedup_scan output."},"drop_id":{"type":"integer","description":"Row ID to DROP (delete). From memory_dedup_scan output."},"merged_value":{"type":"string","description":"Optional. If provided, replaces the KEEP row's value with this consolidated text. If omitted, the KEEP row stays as-is and only the DROP row is deleted."}},"required":["keep_id","drop_id"]}},
    {"name":"memory_search","description":"Search Sean's saved memory for entries matching a query string. Substring match on both keys and values, case-insensitive. Use when Sean asks \"what did I save about X\", \"do I have anything about X in memory\", \"find my notes on X\", or when you need to look up something he previously asked you to remember. Returns up to 20 matches with category, key, value preview, and last-updated date, sorted most-recent-first. Optionally filter to a single category like 'personal', 'work', 'family', 'certificates', 'health', 'finance'.","input_schema":{"type":"object","properties":{"query":{"type":"string","description":"Search string. Substring match, case-insensitive."},"category":{"type":"string","default":"","description":"Optional. Restrict search to one category."}},"required":["query"]}},
    {"name":"recall_recent","description":"Search recent Telegram conversation history for past exchanges containing a substring. Use when Sean references something said or generated earlier (\"we made one last night\", \"that thing we discussed yesterday\", \"the email I sent\") that you don't have in active context. Substring match, case-insensitive, no regex. Returns matching exchanges with timestamps, role, and content snippets. Cap: 20 results, max 168h (7 days) lookback. CRITICAL: ALWAYS call this BEFORE telling Sean something doesn't exist or you don't remember. The rolling history is YOUR limitation, not his mistake.","input_schema":{"type":"object","properties":{"query":{"type":"string"},"hours":{"type":"integer","default":72}},"required":["query"]}},
    {"name":"backlog_add","description":"Append a one-line entry to the Inbox section of the Enhancement Backlog (Clawdia's own development backlog). Use ONLY when YOU (Clawdia) hit a capability gap mid-conversation: a tool you wish existed, a destination you can't write to, a search surface that came up empty for content you suspect exists, an API that returned an error revealing a structural limitation. DO NOT use this for Sean's captures of his own ideas/notes/research/personal todos — those route to notion_add_research with the appropriate category (Personal/Work/Family/Music/Clawdia/Truck/Home/Finance). The Inbox is a slim surface for Clawdia-development triage; it should stay sparse and signal-rich. Entry is auto-timestamped UTC. Always pass a concrete, specific description — 'tool X for Y workflow' is better than 'fix Notion stuff'.","input_schema":{"type":"object","properties":{"text":{"type":"string","description":"One-line description of the capability gap. Be specific."}},"required":["text"]}},
]

# ---- Dispatch wrappers + map ----

def _dispatch_memory_search(inputs):
    _q = inputs.get("query","").strip()
    _cat = inputs.get("category","").strip() or None
    if not _q: return "ERROR: memory_search requires a non-empty query."
    return _memory_search_impl(_q, _cat)


def _dispatch_recall_recent(inputs):
    _q = inputs.get("query","").strip()
    _h = inputs.get("hours", 72)
    if not _q:
        return "ERROR: recall_recent requires a non-empty query string."
    return _recall_recent_impl(_q, _h)


def _dispatch_backlog_add(inputs):
    _text = inputs.get("text") if isinstance(inputs, dict) else None
    if not _text:
        return "ERROR: backlog_add requires text."
    return backlog_add(_text)


DISPATCH = {
    "memory_search": _dispatch_memory_search,
    "recall_recent": _dispatch_recall_recent,
    "backlog_add": _dispatch_backlog_add,
    "memory_dedup_scan": _dispatch_memory_dedup_scan,
    "memory_dedup_merge": _dispatch_memory_dedup_merge,
}
