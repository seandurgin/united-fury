"""Clawdia x_lookup tool: fetch an X post by URL or ID via the X API v2.

Returns full text + metadata. Handles three content body types:
- Articles: article.plain_text (full article body, ~thousands of chars)
- Long-form Premium tweets: note_tweet.text (up to 25K chars)
- Regular tweets: text

Requires X_API_BEARER_TOKEN in env. Each Post:Read costs $0.005 USD,
deduplicated within a 24h UTC window per X API pricing.
"""
import os, re, logging
import requests

log = logging.getLogger("clawdia.x_lookup")

API_BASE = "https://api.x.com/2"
TIMEOUT_SEC = 15

# Field set tuned to avoid X API 503s on Article-type posts.
# Heavier fields (attachments, referenced_tweets, reply_settings, in_reply_to_user_id,
# lang, possibly_sensitive) trigger 503 on some posts; we drop them.
TWEET_FIELDS = (
    "article,author_id,conversation_id,created_at,entities,"
    "note_tweet,public_metrics,text"
)
USER_FIELDS = "name,username,verified"
EXPANSIONS = "author_id"


def _extract_id(url_or_id):
    """Extract numeric post ID from an X/Twitter URL, or accept a bare ID."""
    s = (url_or_id or "").strip()
    if not s:
        return None
    if s.isdigit():
        return s
    m = re.search(r"(?:twitter\.com|x\.com)/[^/]+/status(?:es)?/(\d+)", s)
    if m:
        return m.group(1)
    return None


def lookup_post(url_or_id, max_chars=None):
    """Fetch one X post by URL or numeric ID. Returns formatted text."""
    if not isinstance(max_chars, int) or max_chars <= 0:
        max_chars = 15000

    token = os.environ.get("X_API_BEARER_TOKEN", "")
    if not token:
        return "ERROR: X_API_BEARER_TOKEN not set in /etc/clawdia/env"

    post_id = _extract_id(url_or_id)
    if not post_id:
        return (
            f"ERROR: Could not extract X post ID from {str(url_or_id)[:120]!r}. "
            "Pass a URL like https://x.com/USER/status/12345 or the numeric ID."
        )

    params = {
        "tweet.fields": TWEET_FIELDS,
        "user.fields": USER_FIELDS,
        "expansions": EXPANSIONS,
    }
    log.info("x_lookup: GET tweet %s", post_id)
    try:
        r = requests.get(
            f"{API_BASE}/tweets/{post_id}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT_SEC,
        )
    except requests.exceptions.Timeout:
        return f"ERROR: X API request timed out after {TIMEOUT_SEC}s"
    except requests.exceptions.RequestException as e:
        return f"ERROR: X API request failed: {type(e).__name__}: {str(e)[:200]}"

    if r.status_code == 401:
        return ("ERROR: X API returned 401 Unauthorized. "
                "Bearer token may be revoked, malformed, or invalid.")
    if r.status_code == 403:
        return (f"ERROR: X API returned 403 Forbidden. Possible causes: "
                f"out of credits, app not approved for this endpoint, "
                f"or post is protected. Body: {r.text[:300]}")
    if r.status_code == 404:
        return f"ERROR: X post {post_id} not found (404). Deleted, protected, or never existed."
    if r.status_code == 429:
        return "ERROR: X API returned 429 Too Many Requests (rate limit). Try again in a minute."
    if r.status_code >= 400:
        return f"ERROR: X API returned HTTP {r.status_code}. Body: {r.text[:500]}"

    try:
        data = r.json()
    except ValueError:
        return f"ERROR: X API returned non-JSON response. Body: {r.text[:300]}"

    if "errors" in data and "data" not in data:
        return f"ERROR: X API errors: {data['errors']}"

    tweet = data.get("data", {})
    if not tweet:
        return f"ERROR: X API returned no data for post {post_id}. Response: {str(data)[:300]}"

    article = tweet.get("article") or {}
    note = tweet.get("note_tweet") or {}
    if article.get("plain_text"):
        body = article["plain_text"]
        content_type = "Article"
        title = article.get("title", "")
    elif note.get("text"):
        body = note["text"]
        content_type = "Long-form Post (Premium)"
        title = ""
    else:
        body = tweet.get("text", "")
        content_type = "Post"
        title = ""

    users = (data.get("includes", {}) or {}).get("users", []) or []
    author = next((u for u in users if u.get("id") == tweet.get("author_id")), {})
    author_str = ""
    if author:
        author_str = f"{author.get('name','')} (@{author.get('username','')})"
        if author.get("verified"):
            author_str += " [verified]"

    metrics = tweet.get("public_metrics", {}) or {}
    metric_parts = []
    for k, label in [
        ("impression_count", "views"),
        ("reply_count", "replies"),
        ("retweet_count", "reposts"),
        ("quote_count", "quotes"),
        ("like_count", "likes"),
        ("bookmark_count", "bookmarks"),
    ]:
        v = metrics.get(k)
        if v is not None:
            metric_parts.append(f"{v:,} {label}")
    metrics_str = " | ".join(metric_parts) if metric_parts else "no metrics"

    handle = author.get("username") or "i/web"
    lines = [
        f"X {content_type}",
        f"URL: https://x.com/{handle}/status/{post_id}",
    ]
    if author_str:
        lines.append(f"Author: {author_str}")
    else:
        lines.append(f"Author ID: {tweet.get('author_id','unknown')}")
    lines.append(f"Posted: {tweet.get('created_at','?')}")
    lines.append(f"Metrics: {metrics_str}")
    if title:
        lines.append(f"Title: {title}")
    conv_id = tweet.get("conversation_id")
    if conv_id and conv_id != post_id:
        lines.append(f"Reply in thread: {conv_id} (root post)")
    lines.append("")
    lines.append("Content:")
    if len(body) > max_chars:
        body_out = body[:max_chars] + f"\n\n[Truncated to {max_chars} chars from {len(body)} total]"
    else:
        body_out = body
    lines.append(body_out)

    return "\n".join(lines)
