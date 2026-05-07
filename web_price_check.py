"""Single-URL product info fetcher. Free first, Apify fallback if structured
data isn't found. Prefers JSON-LD Product schema, then Open Graph product
tags, then heuristic regex price hunt."""
import re
import json
import logging
import requests
from urllib.parse import urlparse

log = logging.getLogger("clawdia.web_price_check")

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _extract_jsonld_products(soup):
    """Return list of Product schemas found in <script type='application/ld+json'>."""
    products = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            txt = (script.string or "").strip()
            if not txt:
                continue
            data = json.loads(txt)
        except (json.JSONDecodeError, ValueError):
            continue
        # JSON-LD can be a single object, an array, or wrapped in @graph
        candidates = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                candidates = data["@graph"]
            else:
                candidates = [data]
        for c in candidates:
            if not isinstance(c, dict):
                continue
            t = c.get("@type")
            # @type can be a string or a list
            if isinstance(t, list):
                is_product = any(x.lower() == "product" for x in t if isinstance(x, str))
            elif isinstance(t, str):
                is_product = t.lower() == "product"
            else:
                continue
            if is_product:
                products.append(c)
    return products


def _format_offer(offer):
    """Pull price + availability + currency from an Offer object."""
    if not isinstance(offer, dict):
        return None
    price = offer.get("price") or offer.get("lowPrice")
    currency = offer.get("priceCurrency", "USD")
    availability = offer.get("availability", "")
    # availability is usually a schema.org URL like "https://schema.org/InStock"
    if availability:
        availability = availability.rsplit("/", 1)[-1]
    bits = []
    if price:
        bits.append(f"{currency} {price}")
    if availability:
        bits.append(availability)
    return " — ".join(bits) if bits else None


def _extract_og(soup):
    """Open Graph fallback when no JSON-LD."""
    out = {}
    for tag in soup.find_all("meta"):
        prop = tag.get("property") or tag.get("name") or ""
        content = tag.get("content") or ""
        if not prop or not content:
            continue
        if prop in ("og:title", "og:description", "og:image", "og:site_name",
                    "product:price:amount", "product:price:currency",
                    "product:availability", "twitter:title", "twitter:description"):
            out[prop] = content
    return out


def _heuristic_price_hunt(text):
    """Last-resort regex for $ prices in visible text. Returns up to 3 hits."""
    matches = re.findall(r"\$\s?\d{1,4}(?:,\d{3})*(?:\.\d{2})?", text)
    # de-dup while preserving order
    seen = set()
    out = []
    for m in matches:
        norm = m.replace(" ", "")
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
        if len(out) >= 3:
            break
    return out


def _direct_fetch(url, timeout=15):
    """Try direct fetch + parse. Returns formatted result string OR None if
    nothing structured was found and the page might need JS."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            return f"ERROR_HTTP:{r.status_code}"
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "lxml")
    except Exception as e:
        return f"ERROR_FETCH:{e}"

    domain = urlparse(url).netloc

    # 1. Try JSON-LD Product schema
    products = _extract_jsonld_products(soup)
    if products:
        prod = products[0]
        name = prod.get("name") or "(no name)"
        brand = prod.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name", "")
        elif not isinstance(brand, str):
            brand = ""
        sku = prod.get("sku") or prod.get("mpn") or ""
        desc = (prod.get("description") or "")[:200].strip()

        # Offers can be a single Offer, an array, or AggregateOffer
        offers = prod.get("offers")
        offer_lines = []
        if isinstance(offers, dict):
            if offers.get("@type") == "AggregateOffer":
                low = offers.get("lowPrice")
                high = offers.get("highPrice")
                cur = offers.get("priceCurrency", "USD")
                count = offers.get("offerCount", "?")
                if low and high and low != high:
                    offer_lines.append(f"{cur} {low}\u2013{high} ({count} offers)")
                elif low:
                    offer_lines.append(f"{cur} {low}")
                # Also include nested offers if present
                for sub in (offers.get("offers") or [])[:3]:
                    f = _format_offer(sub)
                    if f: offer_lines.append(f)
            else:
                f = _format_offer(offers)
                if f: offer_lines.append(f)
        elif isinstance(offers, list):
            for o in offers[:3]:
                f = _format_offer(o)
                if f: offer_lines.append(f)

        rating = prod.get("aggregateRating")
        rating_str = ""
        if isinstance(rating, dict):
            rv = rating.get("ratingValue")
            rc = rating.get("reviewCount") or rating.get("ratingCount")
            if rv:
                rating_str = f"\u2605 {rv}" + (f" ({rc} reviews)" if rc else "")

        lines = [f"Product on {domain}:"]
        if brand:
            lines.append(f"  Brand: {brand}")
        lines.append(f"  Name: {name}")
        if sku:
            lines.append(f"  SKU/MPN: {sku}")
        if offer_lines:
            lines.append("  Pricing:")
            for ol in offer_lines:
                lines.append(f"    - {ol}")
        if rating_str:
            lines.append(f"  Rating: {rating_str}")
        if desc:
            lines.append(f"  Description: {desc}")
        lines.append(f"  URL: {url}")
        return "\n".join(lines)

    # 2. Open Graph fallback
    og = _extract_og(soup)
    if og.get("og:title") or og.get("product:price:amount"):
        title = og.get("og:title", "(no title)")
        desc = (og.get("og:description") or og.get("twitter:description") or "")[:200]
        price = og.get("product:price:amount")
        currency = og.get("product:price:currency", "USD")
        availability = og.get("product:availability", "")
        site = og.get("og:site_name", domain)
        lines = [f"Page on {site}:"]
        lines.append(f"  Title: {title}")
        if price:
            lines.append(f"  Price: {currency} {price}")
        if availability:
            lines.append(f"  Availability: {availability}")
        if desc:
            lines.append(f"  Description: {desc}")
        lines.append(f"  URL: {url}")
        return "\n".join(lines)

    # 3. Heuristic price hunt — only return if we find any
    visible_text = soup.get_text(" ", strip=True)
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "(no title)"
    prices = _heuristic_price_hunt(visible_text[:20000])
    if prices:
        lines = [f"Page on {domain} (no structured data, heuristic only):"]
        lines.append(f"  Title: {title}")
        lines.append(f"  Possible prices found in page: {', '.join(prices)}")
        lines.append(f"  URL: {url}")
        return "\n".join(lines)

    # Nothing useful — signal that the caller might want to try Apify
    return None


def web_price_check(url, force_apify=False):
    """Public entry. Returns formatted text describing the product, OR an
    error string. Tries direct fetch first; falls back to Apify generic
    scraper only if direct returns no structured data and force_apify is False
    (Apify costs against the daily cap)."""
    if not url or not isinstance(url, str):
        return "ERROR: web_price_check requires a non-empty URL."
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return "ERROR: URL must start with http:// or https://."

    # Direct fetch first
    if not force_apify:
        result = _direct_fetch(url)
        if result and not result.startswith("ERROR_"):
            return result
        if result and result.startswith("ERROR_"):
            # If HTTP error or fetch error, surface it but offer Apify retry
            return (f"Direct fetch failed: {result}. "
                    "If this site requires JavaScript, retry with force_apify=true "
                    "(uses Apify quota, ~$0.01).")
        # result is None = no structured data found
        return ("Direct fetch returned no product schema/Open Graph/visible prices. "
                "Site may require JavaScript rendering. "
                "Retry with force_apify=true to use Apify (~$0.01).")

    # Apify generic web scraper fallback
    try:
        import os
        token = os.environ.get("APIFY_API_TOKEN", "")
        if not token:
            return "ERROR: APIFY_API_TOKEN not set; cannot use Apify fallback."
        import apify_marketplace as am
        if am._today_call_count() >= am.DAILY_CALL_CAP:
            return f"Daily Apify cap of {am.DAILY_CALL_CAP} calls reached; try direct fetch later."
        # Use cheerio-scraper, lighter than full puppeteer
        api_url = (f"https://api.apify.com/v2/acts/apify~cheerio-scraper/"
                   f"run-sync-get-dataset-items?token={token}&timeout=60")
        payload = {
            "startUrls": [{"url": url}],
            "pageFunction": (
                "async function pageFunction(context) {"
                "  const $ = context.$;"
                "  const title = $('title').text().trim();"
                "  const ld = []; "
                "  $('script[type=\"application/ld+json\"]').each(function(){ ld.push($(this).html()); });"
                "  const og = {};"
                "  $('meta').each(function(){ "
                "    const k = $(this).attr('property') || $(this).attr('name'); "
                "    const v = $(this).attr('content'); "
                "    if (k && v) og[k] = v;"
                "  });"
                "  return { url: context.request.url, title, ld, og, "
                "           text: $('body').text().substring(0, 8000) };"
                "}"
            ),
            "maxRequestRetries": 1,
            "maxPagesPerCrawl": 1,
        }
        r = requests.post(api_url, json=payload, timeout=90)
        am._log_call("apify~cheerio-scraper", 1 if r.ok else 0, "web_price_check")
        if r.status_code not in (200, 201):
            return f"Apify fallback failed: HTTP {r.status_code} {r.text[:200]}"
        data = r.json()
        if not data:
            return "Apify returned no data."
        item = data[0]
        # Reuse the JSON-LD parser by feeding the ld scripts through BeautifulSoup
        from bs4 import BeautifulSoup
        synthetic_html = "<html><head>"
        for ld_script in item.get("ld", []):
            synthetic_html += f'<script type="application/ld+json">{ld_script}</script>'
        for k, v in (item.get("og") or {}).items():
            synthetic_html += f'<meta property="{k}" content="{v}">'
        synthetic_html += f"<title>{item.get('title','')}</title></head><body>"
        synthetic_html += item.get("text", "")
        synthetic_html += "</body></html>"
        soup = BeautifulSoup(synthetic_html, "lxml")
        products = _extract_jsonld_products(soup)
        if products:
            prod = products[0]
            name = prod.get("name") or item.get("title", "(no name)")
            offers = prod.get("offers", {})
            offer_str = _format_offer(offers) if isinstance(offers, dict) else ""
            return (f"Product on {urlparse(url).netloc} (Apify):\n"
                    f"  Name: {name}\n"
                    + (f"  Pricing: {offer_str}\n" if offer_str else "")
                    + f"  URL: {url}")
        # Fall through to og/heuristic on the fetched content
        og = _extract_og(soup)
        if og.get("og:title") or og.get("product:price:amount"):
            return (f"Page on {urlparse(url).netloc} (Apify):\n"
                    f"  Title: {og.get('og:title', item.get('title', '?'))}\n"
                    + (f"  Price: {og.get('product:price:currency','USD')} {og['product:price:amount']}\n"
                       if og.get('product:price:amount') else "")
                    + f"  URL: {url}")
        return (f"Page on {urlparse(url).netloc} (Apify, no structured data):\n"
                f"  Title: {item.get('title', '?')}\n"
                f"  URL: {url}")
    except Exception as e:
        return f"Apify fallback error: {e}"
