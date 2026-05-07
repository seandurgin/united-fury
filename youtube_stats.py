"""YouTube Data API v3 integration for Clawdia — public stats only, no OAuth.

Pulls channel-level snapshot (subs, total views, video count) and recent videos
(title, published, views, likes, comments). Stores a daily snapshot in SQLite
so we can compute day-over-day deltas (e.g. "+12 subs since yesterday").

Cost: ~5 quota units per briefing run, against a 10,000/day free quota.
Effectively free.
"""
import os, json, sqlite3, logging, requests
from datetime import datetime, timezone, timedelta

log = logging.getLogger("clawdia.youtube")

API_BASE = "https://www.googleapis.com/youtube/v3"
DB_PATH = "/var/lib/clawdia/memory.db"


def _key():
    return os.environ.get("YOUTUBE_API_KEY", "")


def _channel_id():
    return os.environ.get("YOUTUBE_CHANNEL_ID", "")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS youtube_snapshots (
        snapshot_date TEXT PRIMARY KEY,
        subscribers INTEGER,
        views INTEGER,
        videos INTEGER,
        recorded_at TEXT NOT NULL
    )""")
    c.commit()
    return c


def _store_snapshot(stats):
    """Idempotent: one row per UTC date. Latest write wins for the day."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO youtube_snapshots "
                "(snapshot_date, subscribers, views, videos, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (today, stats["subscribers"], stats["views"], stats["videos"], now)
            )
    except Exception as e:
        log.warning("snapshot store failed: %s", e)


def _yesterday_snapshot():
    """Return prior day's snapshot dict or None. Looks at most recent prior
    snapshot — a missed day doesn't break the delta, it just compares against
    whatever the most recent prior data is."""
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT snapshot_date, subscribers, views, videos FROM youtube_snapshots "
                "WHERE snapshot_date < date('now') ORDER BY snapshot_date DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return {"date": row[0], "subscribers": row[1], "views": row[2], "videos": row[3]}
    except Exception as e:
        log.warning("snapshot read failed: %s", e)
        return None


def _delta(current, previous, key):
    if not previous: return ""
    diff = current[key] - previous[key]
    if diff == 0: return " (no change)"
    sign = "+" if diff > 0 else ""
    return f" ({sign}{diff} since {previous['date']})"


def _fetch_channel():
    """Returns dict with subscribers/views/videos/title, or raises."""
    r = requests.get(f"{API_BASE}/channels", params={
        "part": "snippet,statistics",
        "id": _channel_id(),
        "key": _key(),
    }, timeout=10)
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        raise RuntimeError(f"No channel found for ID {_channel_id()}")
    it = items[0]
    s = it["statistics"]
    return {
        "title": it["snippet"]["title"],
        "subscribers": int(s.get("subscriberCount", 0)),
        "views": int(s.get("viewCount", 0)),
        "videos": int(s.get("videoCount", 0)),
    }


def _fetch_recent_video_ids(max_results=5):
    """Use search.list (cheap) to get the N most recent video IDs for the channel."""
    r = requests.get(f"{API_BASE}/search", params={
        "part": "snippet",
        "channelId": _channel_id(),
        "order": "date",
        "maxResults": max_results,
        "type": "video",
        "key": _key(),
    }, timeout=10)
    r.raise_for_status()
    return [it["id"]["videoId"] for it in r.json().get("items", [])]


def _fetch_video_stats(video_ids):
    """Batch lookup for views/likes/comments + titles. One quota unit per call."""
    if not video_ids: return []
    r = requests.get(f"{API_BASE}/videos", params={
        "part": "snippet,statistics",
        "id": ",".join(video_ids),
        "key": _key(),
    }, timeout=10)
    r.raise_for_status()
    items = r.json().get("items", [])
    out = []
    for it in items:
        s = it["snippet"]; st = it["statistics"]
        out.append({
            "id": it["id"],
            "title": s["title"],
            "published": s["publishedAt"][:10],
            "views": int(st.get("viewCount", 0)),
            "likes": int(st.get("likeCount", 0)),
            "comments": int(st.get("commentCount", 0)),
        })
    return out


def get_channel_stats():
    """Return channel snapshot + day-over-day deltas. Stores today's snapshot."""
    stats = _fetch_channel()
    prior = _yesterday_snapshot()
    _store_snapshot(stats)
    return {
        **stats,
        "subs_delta": _delta(stats, prior, "subscribers"),
        "views_delta": _delta(stats, prior, "views"),
        "videos_delta": _delta(stats, prior, "videos"),
    }


def get_recent_videos(n=5):
    """Return list of recent video dicts with stats."""
    ids = _fetch_recent_video_ids(n)
    return _fetch_video_stats(ids)


def briefing_section():
    """Format a YouTube section for the morning briefing. Plain text, no markdown
    (briefing.py uses parse_mode=None per the 2026-04-26 fix). Returns a single
    string ready to drop into the briefing template, or an error string."""
    if not _key() or not _channel_id():
        return "(YouTube not configured)"
    try:
        ch = get_channel_stats()
        vids = get_recent_videos(5)
    except Exception as e:
        return f"(YouTube error: {e})"

    lines = [
        f"{ch['title']}: {ch['subscribers']} subs{ch['subs_delta']} • "
        f"{ch['views']:,} total views{ch['views_delta']} • "
        f"{ch['videos']} videos{ch['videos_delta']}",
        "",
        "Recent videos:",
    ]
    for v in vids:
        lines.append(
            f"  • {v['title'][:55]} [{v['published']}] — "
            f"{v['views']:,} views, {v['likes']} likes, {v['comments']} comments"
        )
    return "\n".join(lines)


def for_tool():
    """JSON-friendly output for the on-demand Clawdia tool. Same data, returned
    as a string Claude can read and reason about."""
    return briefing_section()


if __name__ == "__main__":
    # Quick manual test: source env first, then `python3 youtube_stats.py`
    print(briefing_section())


# ──────────────── Comments ────────────────

def _ensure_comments_schema():
    """Idempotent table for tracking which comment IDs we've already shown Sean."""
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS youtube_seen_comments (
            comment_id TEXT PRIMARY KEY,
            video_id TEXT,
            author TEXT,
            text TEXT,
            published TEXT,
            first_seen TEXT NOT NULL
        )""")


def fetch_recent_comments(max_results=20):
    """Fetch up to max_results recent comments across the entire channel.
    Returns list of dicts: [{comment_id, video_id, author, text, published,
    like_count}]."""
    key = _key()
    cid = _channel_id()
    if not key or not cid:
        return [], "YouTube API key or channel ID not set"
    try:
        r = requests.get(
            f"{API_BASE}/commentThreads",
            params={
                "key": key,
                "part": "snippet",
                "allThreadsRelatedToChannelId": cid,
                "maxResults": min(max_results, 100),
                "order": "time",  # most recent first
                "textFormat": "plainText",
            },
            timeout=15
        )
        if r.status_code != 200:
            return [], f"HTTP {r.status_code}: {r.text[:200]}"
        data = r.json()
        items = data.get("items", [])
        out = []
        for item in items:
            sn = item.get("snippet", {})
            top = (sn.get("topLevelComment") or {}).get("snippet", {})
            out.append({
                "comment_id": item.get("id", ""),
                "video_id": sn.get("videoId", ""),
                "author": top.get("authorDisplayName", "?"),
                "text": top.get("textDisplay", ""),
                "published": top.get("publishedAt", ""),
                "like_count": top.get("likeCount", 0),
                "total_replies": sn.get("totalReplyCount", 0),
            })
        return out, None
    except Exception as e:
        return [], str(e)


def get_comments(only_new=True, max_results=20):
    """Fetch recent comments; if only_new, return only those not yet recorded
    in youtube_seen_comments. Marks returned items as seen so subsequent calls
    don't re-show them."""
    _ensure_comments_schema()
    comments, err = fetch_recent_comments(max_results)
    if err:
        return f"YouTube comments error: {err}"
    if not comments:
        return "No comments on the channel yet."

    truly_new = []
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        for cm in comments:
            cid = cm["comment_id"]
            if not cid:
                continue
            if only_new:
                exists = c.execute(
                    "SELECT 1 FROM youtube_seen_comments WHERE comment_id=?",
                    (cid,)
                ).fetchone()
                if exists:
                    continue
            truly_new.append(cm)
            c.execute(
                "INSERT OR IGNORE INTO youtube_seen_comments "
                "(comment_id, video_id, author, text, published, first_seen) "
                "VALUES (?,?,?,?,?,?)",
                (cid, cm["video_id"], cm["author"], cm["text"][:500],
                 cm["published"], now)
            )

    display = truly_new if only_new else comments
    if not display:
        return f"No new comments since last check (most recent: {comments[0]['published'][:10]})."

    label = "NEW comments" if only_new else "Recent comments"
    lines = [f"{len(display)} {label} on Hollowed Ground:"]
    for cm in display[:max_results]:
        text = cm["text"].replace("\n", " ").strip()
        if len(text) > 200:
            text = text[:200] + "…"
        likes = f" ❤{cm['like_count']}" if cm["like_count"] else ""
        replies = f" ({cm['total_replies']} replies)" if cm["total_replies"] else ""
        published = cm["published"][:10] if cm["published"] else "?"
        lines.append(f"  • {cm['author']} on {published}{likes}{replies}: \"{text}\"")
    return "\n".join(lines)


def comments_briefing_section():
    """One-line summary for morning briefing: count of new comments since
    last briefing, or quietly returns None if no new comments."""
    _ensure_comments_schema()
    comments, err = fetch_recent_comments(max_results=20)
    if err or not comments:
        return None
    with _conn() as c:
        new_count = 0
        for cm in comments:
            cid = cm["comment_id"]
            if not cid:
                continue
            exists = c.execute(
                "SELECT 1 FROM youtube_seen_comments WHERE comment_id=?",
                (cid,)
            ).fetchone()
            if not exists:
                new_count += 1
    if new_count == 0:
        return None
    return f"  💬 {new_count} new comment(s) on Hollowed Ground (use youtube_comments to see them)"
