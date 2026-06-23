"""Clawdia web_fetch tool: fetch a URL, return text content.

Works for most plain web content (blogs, articles, docs, GitHub READMEs,
public JSON APIs). May fail or return a login wall for sites that block
unauthenticated scraping (X/Twitter, LinkedIn, paywalled news).

When the tool returns ERROR or a login-wall page, surface that honestly per
Shape 1 -- do not paraphrase the actual content from prior knowledge.
"""
import logging
import re
import requests

log = logging.getLogger("clawdia.web_fetch")

UA = "Mozilla/5.0 (compatible; Clawdia/1.0; +https://github.com/seandurgin/clawdia)"
DEFAULT_MAX = 15000
TIMEOUT_SEC = 15


def fetch(url, max_chars=None):
    """Fetch a URL and return formatted text content.

    Returns a string with URL/status/content-type header followed by the body
    (HTML stripped to text). On any error, returns 'ERROR: ...' so the caller
    can surface it verbatim.
    """
    if max_chars is None or not isinstance(max_chars, int) or max_chars <= 0:
        max_chars = DEFAULT_MAX
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return f"ERROR: URL must start with http:// or https://. Got: {str(url)[:200]}"

    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,text/plain,application/json,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    log.info("web_fetch: GET %s (max_chars=%d)", url, max_chars)
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT_SEC, allow_redirects=True)
    except requests.exceptions.Timeout:
        return f"ERROR: Request to {url} timed out after {TIMEOUT_SEC}s"
    except requests.exceptions.SSLError as e:
        return f"ERROR: SSL error for {url}: {str(e)[:200]}"
    except requests.exceptions.ConnectionError as e:
        return f"ERROR: Connection failed for {url}: {str(e)[:200]}"
    except requests.exceptions.RequestException as e:
        return f"ERROR: Request failed for {url}: {type(e).__name__}: {str(e)[:200]}"

    ctype = r.headers.get("Content-Type", "").lower()
    final_url = r.url

    if r.status_code >= 400:
        body_preview = (r.text or "")[:500]
        return (
            f"URL: {url}\n"
            f"Final URL: {final_url}\n"
            f"Status: HTTP {r.status_code} (FAILED)\n"
            f"Content-Type: {ctype}\n\n"
            f"Body preview:\n{body_preview}"
        )

    notes = ""
    if any(t in ctype for t in ("text/html", "application/xhtml")):
        text = _html_to_text(r.text)
        lower = text.lower()
        # Heuristic signals of an interstitial / login wall / JS-only page
        if any(s in lower for s in (
            "log in to twitter", "sign up to see", "javascript is not available",
            "log in to x", "create account", "enable javascript",
            "please enable javascript", "you need to enable javascript",
        )) and len(text) < 4000:
            notes = (
                "\n\nHEURISTIC NOTE: response looks like a login wall or JS-only page. "
                "The site may block unauthenticated scraping. The text above is what was "
                "actually returned -- do not paraphrase the page's intended content from "
                "prior knowledge (Shape 1)."
            )
    elif "application/json" in ctype:
        text = r.text
    elif ctype.startswith("text/"):
        text = r.text
    else:
        return (
            f"URL: {url}\n"
            f"Final URL: {final_url}\n"
            f"Status: {r.status_code}\n"
            f"Content-Type: {ctype}\n\n"
            f"ERROR: Unsupported content type. Only HTML, plain text, and JSON are "
            f"supported. Binary content (images, PDFs, etc.) is not handled by this tool."
        )

    original_len = len(text)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[Truncated to {max_chars} chars from {original_len} total]"

    return (
        f"URL: {url}\n"
        f"Final URL: {final_url}\n"
        f"Status: {r.status_code}\n"
        f"Content-Type: {ctype}\n"
        f"Length: {original_len} chars (text only)\n\n"
        f"{text}{notes}"
    )


def _html_to_text(html):
    """Strip HTML to readable text. Crude but reliable; preserves paragraph breaks."""
    # Remove scripts/styles/noscript entirely (they hide structure noise + inline JS)
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<noscript\b[^>]*>.*?</noscript>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Convert block elements to newlines so reading structure survives the strip
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</p>", "\n\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</li>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</h[1-6]>", "\n\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</div>", "\n", html, flags=re.IGNORECASE)
    # Strip all remaining tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Decode common HTML entities
    html = (html
            .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&#39;", "'").replace("&apos;", "'")
            .replace("&nbsp;", " ").replace("&mdash;", "--").replace("&ndash;", "-")
            .replace("&hellip;", "...").replace("&rsquo;", "'").replace("&lsquo;", "'")
            .replace("&rdquo;", '"').replace("&ldquo;", '"'))
    # Numeric entities (basic)
    html = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))) if int(m.group(1)) < 0x10FFFF else "", html)
    # Collapse whitespace per-line, then collapse blank-line runs
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in html.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
