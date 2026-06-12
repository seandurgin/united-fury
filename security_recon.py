"""
Security & recon tools (DNS hygiene, TLS audit, CVE enrichment).

Extracted from bot_new.py 2026-06-11 as part of the modularization effort.
All functions are pure / read-only / network-IO. No shared state with the
main bot beyond /etc/clawdia/scan_allowlist.json.
"""
import os, ssl, socket, json, re, time, base64, urllib.parse
from datetime import datetime, timezone, timedelta
import requests

# Optional deps — present in bot_new.py's environment
try:
    import dns.resolver
    import dns.exception
except ImportError:
    dns = None


# ---- Module-level constants ----

_KEV_CACHE_PATH = "/var/lib/clawdia/kev_cache.json"

_KEV_CACHE_TTL_SEC = 24 * 3600

_SCAN_ALLOWLIST_PATH = "/etc/clawdia/scan_allowlist.json"
_SCAN_ALLOWLIST_CACHE = None
_SCAN_ALLOWLIST_MTIME = 0

_SPF_PROVIDER_INCLUDES = {
    # Mail providers
    "easywp": "include:spf.easywp.com",
    "google": "include:_spf.google.com",
    "googleworkspace": "include:_spf.google.com",
    "gworkspace": "include:_spf.google.com",
    "gmail": "include:_spf.google.com",
    "outlook": "include:spf.protection.outlook.com",
    "microsoft365": "include:spf.protection.outlook.com",
    "m365": "include:spf.protection.outlook.com",
    "office365": "include:spf.protection.outlook.com",
    "icloud": "include:icloud.com",
    "applemail": "include:icloud.com",
    "fastmail": "include:spf.messagingengine.com",
    "zoho": "include:zoho.com",
    "protonmail": "include:_spf.protonmail.ch",
    "proton": "include:_spf.protonmail.ch",
    "tutanota": "include:spf.tutanota.de",
    "namecheap-privatemail": "include:spf.privatemail.com",
    "namecheap-forwarding": "include:spf.efwd.registrar-servers.com",
    # Transactional / marketing
    "mailchimp": "include:servers.mcsv.net",
    "sendgrid": "include:sendgrid.net",
    "mailgun": "include:mailgun.org",
    "postmark": "include:spf.mtasv.net",
    "ses": "include:amazonses.com",
    "amazonses": "include:amazonses.com",
    "sparkpost": "include:sparkpostmail.com",
    "mailjet": "include:spf.mailjet.com",
    "constant-contact": "include:spf.constantcontact.com",
    "convertkit": "include:_spf.convertkit.com",
    "klaviyo": "include:_spf.klaviyo.com",
    "intercom": "include:_spf.intercom.io",
    "drift": "include:_spf.drift.com",
    "hubspot": "include:_spf.hubspot.com",
    "salesforce": "include:_spf.salesforce.com",
    "freshdesk": "include:email.freshdesk.com",
    "zendesk": "include:mail.zendesk.com",
    "shopify": "include:shops.shopify.com",
    "github": "include:_spf.github.com",
}

_DKIM_COMMON_SELECTORS = [
    "default", "selector1", "selector2",        # generic + Microsoft 365
    "google", "20210112",                       # Google Workspace
    "k1", "k2", "k3",                           # Mailchimp, common gen
    "easywp", "wp1", "wp2",                     # EasyWP / WordPress
    "dkim", "mail", "email",                    # generic fallback names
    "mxvault", "mxvault2",                      # Namecheap PrivateMail
    "smtpapi", "s1", "s2",                      # SendGrid
    "krs", "krs1", "krs2",                      # Mailgun (Krs key rotation)
    "fm1", "fm2", "fm3",                        # Fastmail
    "protonmail", "protonmail2", "protonmail3", # ProtonMail
    "ic1", "ic2",                               # iCloud
    "amazonses", "ses",                         # Amazon SES
    "zoho", "zoho1",                            # Zoho
]


# ---- Function implementations ----

def _normalize_cve_id(cve_id):
    """Accept 'CVE-2024-3094', 'cve-2024-3094', or '2024-3094'; return canonical 'CVE-2024-3094'."""
    if not cve_id:
        return None
    s = str(cve_id).strip().upper()
    if not s:
        return None
    if not s.startswith("CVE-"):
        s = "CVE-" + s
    if not re.match(r"^CVE-\d{4}-\d{4,}$", s):
        return None
    return s

def epss_lookup(cve_id):
    """Look up FIRST.org EPSS score for a CVE.

    Returns probability (0-1) that the CVE will be exploited in the next 30 days,
    plus its percentile rank among all CVEs. EPSS v4 (production since 2025-03-17).
    """
    import requests
    norm = _normalize_cve_id(cve_id)
    if not norm:
        return f"ERROR: invalid CVE ID {cve_id!r}. Expected format: CVE-YYYY-NNNN."
    try:
        r = requests.get(
            "https://api.first.org/data/v1/epss",
            params={"cve": norm},
            timeout=10,
        )
        if r.status_code != 200:
            return f"EPSS HTTP {r.status_code}: {r.text[:200]}"
        data = r.json()
        if data.get("status") != "OK":
            return f"EPSS status={data.get('status')}: {data.get('message', '(no message)')}"
        entries = data.get("data", [])
        if not entries:
            return f"No EPSS data for {norm} (CVE may be too new or not scored)."
        e = entries[0]
        epss = float(e.get("epss", 0))
        pct = float(e.get("percentile", 0))
        date = e.get("date", "?")
        # Interpretation
        if epss >= 0.5:
            interp = "HIGH exploitation likelihood"
        elif epss >= 0.1:
            interp = "moderate exploitation likelihood"
        elif epss >= 0.01:
            interp = "low-moderate exploitation likelihood"
        else:
            interp = "low exploitation likelihood"
        return (f"{norm}: EPSS={epss:.4f} ({epss*100:.2f}% chance of exploitation in 30 days), "
                f"percentile={pct:.4f} (higher than {pct*100:.2f}% of all CVEs). "
                f"{interp}. As of {date}.")
    except Exception as e:
        return f"epss_lookup error: {e}"

def _kev_get_catalog():
    """Return CISA KEV catalog dict, refreshing the disk cache if stale (>24h old).

    Cache file: /var/lib/clawdia/kev_cache.json
    Returns the full catalog object so callers can index by cveID.
    """
    import os, json, time, requests
    refresh = True
    if os.path.exists(_KEV_CACHE_PATH):
        age = time.time() - os.path.getmtime(_KEV_CACHE_PATH)
        if age < _KEV_CACHE_TTL_SEC:
            refresh = False
    if refresh:
        r = requests.get(
            "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
            timeout=20,
        )
        if r.status_code != 200:
            # Fall back to stale cache if available
            if os.path.exists(_KEV_CACHE_PATH):
                with open(_KEV_CACHE_PATH) as f:
                    return json.load(f)
            raise RuntimeError(f"KEV fetch HTTP {r.status_code}")
        os.makedirs(os.path.dirname(_KEV_CACHE_PATH), exist_ok=True)
        with open(_KEV_CACHE_PATH, "w") as f:
            f.write(r.text)
    with open(_KEV_CACHE_PATH) as f:
        return json.load(f)

def kev_check(cve_id):
    """Check whether a CVE is on CISA's Known Exploited Vulnerabilities (KEV) catalog.

    KEV entries are CVEs confirmed exploited in the wild. Per BOD 22-01 they have
    a federal remediation deadline (typically 2-4 weeks from listing).
    """
    norm = _normalize_cve_id(cve_id)
    if not norm:
        return f"ERROR: invalid CVE ID {cve_id!r}."
    try:
        catalog = _kev_get_catalog()
        vulns = catalog.get("vulnerabilities", [])
        match = next((v for v in vulns if v.get("cveID") == norm), None)
        if not match:
            return f"{norm}: NOT on CISA KEV catalog ({len(vulns)} entries checked, catalogVersion={catalog.get('catalogVersion','?')})."
        ransom = match.get("knownRansomwareCampaignUse", "Unknown")
        return (
            f"{norm}: ON CISA KEV catalog.\n"
            f"  Vendor: {match.get('vendorProject','?')}\n"
            f"  Product: {match.get('product','?')}\n"
            f"  Name: {match.get('vulnerabilityName','?')}\n"
            f"  Added to KEV: {match.get('dateAdded','?')}\n"
            f"  Federal due date: {match.get('dueDate','?')}\n"
            f"  Ransomware use: {ransom}\n"
            f"  Description: {match.get('shortDescription','(none)')[:300]}\n"
            f"  Required action: {match.get('requiredAction','(none)')[:300]}"
        )
    except Exception as e:
        return f"kev_check error: {e}"

def cve_lookup(cve_id):
    """Look up full CVE record from NVD (NIST National Vulnerability Database).

    Returns description, CVSS v3.1 (or v2 fallback), CWE weaknesses, published date,
    and a sample of references. Unauthenticated NVD API: ~5 req/30s rate limit.
    """
    import requests
    norm = _normalize_cve_id(cve_id)
    if not norm:
        return f"ERROR: invalid CVE ID {cve_id!r}."
    try:
        r = requests.get(
            "https://services.nvd.nist.gov/rest/json/cves/2.0",
            params={"cveId": norm},
            timeout=15,
        )
        if r.status_code == 404:
            return f"{norm}: not found in NVD."
        if r.status_code != 200:
            return f"NVD HTTP {r.status_code}: {r.text[:200]}"
        data = r.json()
        if data.get("totalResults", 0) == 0:
            return f"{norm}: not found in NVD."
        cve = data["vulnerabilities"][0]["cve"]
        desc = next((d["value"] for d in cve.get("descriptions", [])
                     if d.get("lang") == "en"), "(no description)")
        published = (cve.get("published", "?") or "?")[:10]
        modified = (cve.get("lastModified", "?") or "?")[:10]
        status = cve.get("vulnStatus", "?")
        # CVSS extraction - prefer v3.1 then v3.0 then v2
        cvss_str = "(no CVSS score)"
        metrics = cve.get("metrics", {})
        for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
            if key in metrics and metrics[key]:
                m = metrics[key][0].get("cvssData", {})
                base = m.get("baseScore", "?")
                sev = m.get("baseSeverity") or metrics[key][0].get("baseSeverity", "?")
                vec = m.get("vectorString", "")
                cvss_str = f"CVSS {key.replace('cvssMetric','v').replace('V','v')}: {base} ({sev}) {vec}"
                break
        # CWE
        cwes = []
        for w in cve.get("weaknesses", []):
            for d in w.get("description", []):
                if d.get("lang") == "en" and d.get("value", "").startswith("CWE-"):
                    cwes.append(d["value"])
        cwe_str = ", ".join(sorted(set(cwes))) if cwes else "(none listed)"
        # References - just count and show top 3
        refs = cve.get("references", [])
        ref_lines = [f"  - {r.get('url','')[:120]}" for r in refs[:3]]
        ref_more = f" (+{len(refs)-3} more)" if len(refs) > 3 else ""
        return (
            f"{norm} (NVD status: {status})\n"
            f"  Published: {published}, Last modified: {modified}\n"
            f"  {cvss_str}\n"
            f"  CWE: {cwe_str}\n"
            f"  Description: {desc[:500]}\n"
            f"  References ({len(refs)} total){ref_more}:\n" + "\n".join(ref_lines)
        )
    except Exception as e:
        return f"cve_lookup error: {e}"

def cve_enrich(cve_id):
    """One-call combined enrichment: NVD details + EPSS + KEV + priority guidance.

    This is the right tool when Sean asks 'tell me about CVE-X' or 'how bad is CVE-X'.
    Combines all three sources into a single prioritized summary with action guidance.
    """
    import requests
    norm = _normalize_cve_id(cve_id)
    if not norm:
        return f"ERROR: invalid CVE ID {cve_id!r}."
    # Fetch all three; degrade gracefully if any fail
    nvd_result = cve_lookup(norm)
    epss_result = epss_lookup(norm)
    kev_result = kev_check(norm)
    # Extract priority signals from the structured strings we just generated
    is_kev = kev_result.startswith(f"{norm}: ON CISA KEV")
    epss_value = None
    m = re.search(r"EPSS=([0-9.]+)", epss_result)
    if m:
        try:
            epss_value = float(m.group(1))
        except ValueError:
            epss_value = None
    cvss_value = None
    cvss_sev = None
    m = re.search(r"CVSS [^:]+: ([0-9.]+) \(([A-Z]+)\)", nvd_result)
    if m:
        try:
            cvss_value = float(m.group(1))
            cvss_sev = m.group(2)
        except ValueError:
            pass
    # Decision: industry-common rule = patch within KEV SLA (14 days) if
    #   (CVSS >= 7) AND (KEV OR EPSS >= 0.5).
    if is_kev:
        priority = "CRITICAL — actively exploited (CISA KEV). Patch within federal SLA window (typically 14 days from listing)."
    elif cvss_value and cvss_value >= 7 and epss_value and epss_value >= 0.5:
        priority = "HIGH — high CVSS + high exploitation probability. Patch within 14 days."
    elif cvss_value and cvss_value >= 7 and epss_value and epss_value >= 0.1:
        priority = "HIGH — high CVSS + moderate exploitation probability. Patch within 30 days."
    elif cvss_value and cvss_value >= 7:
        priority = "MODERATE — high CVSS but low exploitation probability. Patch in standard cycle (30-60 days)."
    elif epss_value and epss_value >= 0.5:
        priority = "MODERATE — high exploitation probability despite lower CVSS. Watch closely; patch promptly."
    elif cvss_value and cvss_value >= 4:
        priority = "LOW-MODERATE — moderate CVSS, low exploitation probability. Standard remediation cycle."
    else:
        priority = "LOW — patch in normal cycle. Monitor EPSS over time."
    return (
        f"=== CVE Enrichment for {norm} ===\n\n"
        f"PRIORITY: {priority}\n\n"
        f"--- NVD ---\n{nvd_result}\n\n"
        f"--- EPSS ---\n{epss_result}\n\n"
        f"--- CISA KEV ---\n{kev_result}"
    )

def _load_scan_allowlist():
    """Load and cache the scan allowlist from disk. Reloads if file mtime changes."""
    import os, json
    global _SCAN_ALLOWLIST_CACHE, _SCAN_ALLOWLIST_MTIME
    if not os.path.exists(_SCAN_ALLOWLIST_PATH):
        return {"exact_hosts": [], "domains_with_subdomains": []}
    mtime = os.path.getmtime(_SCAN_ALLOWLIST_PATH)
    if _SCAN_ALLOWLIST_CACHE is None or mtime != _SCAN_ALLOWLIST_MTIME:
        with open(_SCAN_ALLOWLIST_PATH) as f:
            data = json.load(f)
        _SCAN_ALLOWLIST_CACHE = {
            "exact_hosts": set(data.get("exact_hosts", [])),
            "domains_with_subdomains": set(data.get("domains_with_subdomains", [])),
        }
        _SCAN_ALLOWLIST_MTIME = mtime
    return _SCAN_ALLOWLIST_CACHE

def _check_scan_target(target):
    """Return (True, normalized_target) if target is on allowlist; else (False, reason).

    Normalizes by lowercasing and stripping protocol/port/path. Refuses anything
    that smells suspicious (private IPs not on allowlist, multicast, broadcast).
    """
    if not target or not isinstance(target, str):
        return (False, "target must be a non-empty string")
    # Strip protocol, port, path
    t = target.strip().lower()
    t = re.sub(r"^https?://", "", t)
    t = re.sub(r"^[a-z]+://", "", t)  # strip any other scheme
    t = t.split("/")[0]  # strip path
    t = t.split("?")[0]  # strip query
    # Handle port (host:port). Keep IPv6 brackets intact.
    if t.startswith("["):
        # IPv6 in brackets: [::1]:80 -> ::1
        m = re.match(r"\[([^\]]+)\](?::\d+)?$", t)
        if m:
            t = m.group(1)
    elif t.count(":") == 1:
        # host:port (only single colon - IPv6 raw addresses have many colons)
        t = t.split(":")[0]
    if not t:
        return (False, "target empty after normalization")
    # Block obviously bad targets even if accidentally in allowlist
    if t in ("0.0.0.0", "255.255.255.255"):
        return (False, f"{t!r} is broadcast/wildcard, refused")
    if t.startswith("169.254.") or t.startswith("224.") or t.startswith("239."):
        return (False, f"{t!r} is link-local/multicast, refused")
    allow = _load_scan_allowlist()
    if t in allow["exact_hosts"]:
        return (True, t)
    for dom in allow["domains_with_subdomains"]:
        if t == dom or t.endswith("." + dom):
            return (True, t)
    # Show user what allowlist looks like so they can fix scope if legit
    return (False,
        f"{t!r} is NOT on /etc/clawdia/scan_allowlist.json. "
        f"Allowed hosts: {sorted(allow['exact_hosts'])}. "
        f"Allowed domains (with subdomains): {sorted(allow['domains_with_subdomains'])}. "
        f"To add a target, SSH to clawdia VPS and edit the file as root, then restart clawdia.")

def dns_audit(domain):
    """Audit DNS hygiene for a domain: A/AAAA/MX/NS/SOA/SPF/DMARC/DKIM-pattern checks.

    Returns findings prioritized by severity. Catches: missing SPF/DMARC, weak SPF
    (~all instead of -all), missing DKIM patterns, zone transfer attempts.
    """
    ok, t = _check_scan_target(domain)
    if not ok:
        return f"REFUSED: {t}"
    try:
        import dns.resolver, dns.exception
    except ImportError:
        return "ERROR: dnspython not installed (pip install dnspython)"

    findings = []
    summary_lines = [f"=== DNS audit: {t} ==="]

    def query(qtype):
        try:
            answers = dns.resolver.resolve(t, qtype, lifetime=5)
            return [str(r) for r in answers]
        except dns.resolver.NoAnswer:
            return []
        except dns.resolver.NXDOMAIN:
            return None  # signal: doesn't exist
        except dns.exception.DNSException as e:
            return f"ERROR: {e}"

    a = query("A")
    aaaa = query("AAAA")
    mx = query("MX")
    ns = query("NS")
    soa = query("SOA")
    txt = query("TXT") or []

    if a is None:
        return f"NXDOMAIN: {t} does not exist in DNS."
    summary_lines.append(f"A records: {a or '(none)'}")
    summary_lines.append(f"AAAA records: {aaaa or '(none)'}")
    summary_lines.append(f"MX records: {mx or '(none)'}")
    summary_lines.append(f"NS records: {ns or '(none)'}")
    summary_lines.append(f"SOA: {soa[0] if soa else '(none)'}")

    # SPF check
    spf = [r for r in txt if r.lower().strip('"').startswith("v=spf1")]
    if not spf:
        findings.append("HIGH: No SPF record found. Email sent from unauthorized servers cannot be rejected by receivers. (CWE-290)")
    else:
        spf_rec = spf[0]
        summary_lines.append(f"SPF: {spf_rec[:200]}")
        if "~all" in spf_rec:
            findings.append("MEDIUM: SPF uses ~all (softfail). Receivers may accept spoofed mail. Consider -all (hardfail). (CIS Control 9.4)")
        elif "?all" in spf_rec:
            findings.append("MEDIUM: SPF uses ?all (neutral). Provides no spoofing protection. Use -all or ~all.")
        elif "+all" in spf_rec:
            findings.append("CRITICAL: SPF uses +all - allows ANY server to send mail as you. (CWE-290)")
        # Lookup count check (SPF spec: max 10 DNS lookups)
        lookup_terms = re.findall(r"\b(include|a|mx|exists|redirect|ptr):", spf_rec)
        if len(lookup_terms) > 10:
            findings.append(f"MEDIUM: SPF has {len(lookup_terms)} DNS lookups (RFC 7208 max is 10).")

    # DMARC check (lives at _dmarc.domain)
    dmarc = query("TXT")  # this is for the same domain
    try:
        dmarc_answers = dns.resolver.resolve(f"_dmarc.{t}", "TXT", lifetime=5)
        dmarc_rec = [str(r) for r in dmarc_answers]
        d = next((r for r in dmarc_rec if r.lower().strip('"').startswith("v=dmarc1")), None)
        if d:
            summary_lines.append(f"DMARC: {d[:200]}")
            if "p=none" in d.lower():
                findings.append("MEDIUM: DMARC policy is p=none (monitor only). Mail spoofing failures are not rejected. Move to p=quarantine then p=reject. (NIST SP 800-177)")
            elif "p=quarantine" in d.lower():
                findings.append("LOW: DMARC policy is p=quarantine. Consider p=reject for stricter enforcement.")
        else:
            findings.append("HIGH: _dmarc subdomain returned TXT but no v=DMARC1 record. (CWE-290)")
    except dns.resolver.NXDOMAIN:
        findings.append("HIGH: No DMARC record at _dmarc.{} - email spoofing not policed. (NIST SP 800-177)".format(t))
    except dns.resolver.NoAnswer:
        findings.append("HIGH: _dmarc.{} exists but has no TXT records.".format(t))
    except dns.exception.DNSException as e:
        findings.append(f"INFO: DMARC lookup error: {e}")

    # MTA-STS (RFC 8461) - presence is good practice for sites that send mail
    if mx:
        try:
            mta_sts = dns.resolver.resolve(f"_mta-sts.{t}", "TXT", lifetime=5)
            summary_lines.append("MTA-STS: configured")
        except dns.exception.DNSException:
            findings.append("LOW: No MTA-STS record - TLS for inbound mail is opportunistic, not enforced. (RFC 8461)")

    # Build output
    summary_lines.append("")
    summary_lines.append(f"--- Findings ({len(findings)}) ---")
    if not findings:
        summary_lines.append("No issues detected. DNS hygiene looks good.")
    else:
        for f in findings:
            summary_lines.append(f"  {f}")
    return "\n".join(summary_lines)

def cert_check(host):
    """Check TLS certificate and configuration for a host:port (default 443).

    Returns cert subject, issuer, expiry, SANs, and findings on weak config.
    """
    if ":" in host and not host.startswith("["):
        h, _, p = host.rpartition(":")
        try:
            port = int(p)
        except ValueError:
            ok, t = _check_scan_target(host)
            return f"REFUSED: {t}" if not ok else f"ERROR: invalid port in {host!r}"
        check_target = h
    else:
        port = 443
        check_target = host
    ok, t = _check_scan_target(check_target)
    if not ok:
        return f"REFUSED: {t}"
    try:
        import ssl, socket
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.x509.oid import NameOID, ExtensionOID
        import datetime
    except ImportError as e:
        return f"ERROR: missing crypto deps: {e}"

    findings = []
    summary = [f"=== TLS cert check: {t}:{port} ==="]

    ctx = ssl.create_default_context()
    ctx.check_hostname = False  # We want to grab the cert even if hostname mismatch (so we can REPORT mismatch)
    ctx.verify_mode = ssl.CERT_NONE  # Same reason

    try:
        with socket.create_connection((t, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=t) as ssock:
                der = ssock.getpeercert(binary_form=True)
                proto = ssock.version()
                cipher = ssock.cipher()
                summary.append(f"TLS protocol: {proto}")
                summary.append(f"Cipher: {cipher[0]} ({cipher[2]}-bit)")
                if proto in ("TLSv1", "TLSv1.1", "SSLv3"):
                    findings.append(f"HIGH: deprecated {proto} accepted. Disable. (CWE-326, CIS Control 4.10)")
                elif proto == "TLSv1.2":
                    findings.append("LOW: TLS 1.2 accepted (still OK but TLS 1.3 preferred).")
        cert = x509.load_der_x509_certificate(der)
        # Subject + issuer + SAN
        cn = next((a.value for a in cert.subject if a.oid == NameOID.COMMON_NAME), "(none)")
        issuer = next((a.value for a in cert.issuer if a.oid == NameOID.COMMON_NAME), "(none)")
        summary.append(f"Subject CN: {cn}")
        summary.append(f"Issuer: {issuer}")
        try:
            san_ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
            sans = [n.value for n in san_ext.value]
            summary.append(f"SANs ({len(sans)}): {', '.join(sans[:10])}{'...' if len(sans) > 10 else ''}")
            # Hostname match: does t appear in CN or SANs?
            t_lower = t.lower()
            matched = (t_lower == cn.lower() or
                       any(s.lower() == t_lower for s in sans) or
                       any(s.startswith("*.") and t_lower.endswith(s[1:].lower()) for s in sans))
            if not matched:
                findings.append(f"HIGH: hostname {t!r} does NOT match CN or any SAN. Connection would fail strict validation. (CWE-297)")
        except x509.ExtensionNotFound:
            sans = []
            findings.append("HIGH: certificate has no Subject Alternative Names. Modern browsers reject CN-only certs. (CWE-295)")
        # Expiry
        now = datetime.datetime.now(datetime.timezone.utc)
        # cryptography 42+ uses not_valid_before_utc/not_valid_after_utc
        try:
            nva = cert.not_valid_after_utc
            nvb = cert.not_valid_before_utc
        except AttributeError:
            nva = cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
            nvb = cert.not_valid_before.replace(tzinfo=datetime.timezone.utc)
        summary.append(f"Valid from: {nvb.isoformat()}")
        summary.append(f"Valid to:   {nva.isoformat()}")
        days_remaining = (nva - now).days
        summary.append(f"Days until expiry: {days_remaining}")
        if days_remaining < 0:
            findings.append(f"CRITICAL: certificate EXPIRED {-days_remaining} days ago. (CWE-298)")
        elif days_remaining < 7:
            findings.append(f"HIGH: certificate expires in {days_remaining} days - URGENT renewal needed.")
        elif days_remaining < 30:
            findings.append(f"MEDIUM: certificate expires in {days_remaining} days - schedule renewal.")
        # Signature algorithm
        sig_algo = cert.signature_hash_algorithm.name if cert.signature_hash_algorithm else "(unknown)"
        summary.append(f"Signature algorithm: {sig_algo}")
        if sig_algo in ("md5", "sha1"):
            findings.append(f"HIGH: weak signature algorithm {sig_algo}. (CWE-327)")
        # Public key size
        pk = cert.public_key()
        try:
            ksize = pk.key_size
            summary.append(f"Public key size: {ksize} bits")
            if "RSA" in str(type(pk)) and ksize < 2048:
                findings.append(f"HIGH: RSA key size {ksize} < 2048 bits. (CWE-326)")
        except AttributeError:
            pass
    except socket.timeout:
        return f"ERROR: connection to {t}:{port} timed out"
    except (ConnectionRefusedError, OSError) as e:
        return f"ERROR: cannot connect to {t}:{port} - {e}"
    except ssl.SSLError as e:
        return f"ERROR: TLS handshake failed - {e}"
    except Exception as e:
        return f"ERROR: cert_check error - {e}"

    summary.append("")
    summary.append(f"--- Findings ({len(findings)}) ---")
    if not findings:
        summary.append("No issues detected. TLS configuration is solid.")
    else:
        for f in findings:
            summary.append(f"  {f}")
    return "\n".join(summary)

def subdomain_enum(domain):
    """Passive subdomain enumeration via Certificate Transparency logs (crt.sh).

    Returns unique subdomains observed in CT logs. Zero scanning - just queries
    a public CT log aggregator. Useful for asset discovery on a domain you own.
    """
    ok, t = _check_scan_target(domain)
    if not ok:
        return f"REFUSED: {t}"
    import requests
    try:
        r = requests.get(
            "https://crt.sh/",
            params={"q": f"%.{t}", "output": "json"},
            timeout=30,
            headers={"User-Agent": "Clawdia/1.0 (personal security tool)"},
        )
        if r.status_code != 200:
            return f"crt.sh HTTP {r.status_code}: {r.text[:200]}"
        # crt.sh sometimes returns malformed JSON when results are massive; tolerate
        try:
            data = r.json()
        except ValueError:
            return f"crt.sh returned non-JSON response (often means too many results). Try a more specific domain."
        if not data:
            return f"No CT log entries found for {t}. The domain may be new or unused."
        subs = set()
        for entry in data:
            # name_value can contain multiple names separated by newlines
            for name in (entry.get("name_value", "") or "").split("\n"):
                name = name.strip().lower().lstrip("*.")
                if name and name != t and (name == t or name.endswith("." + t)):
                    subs.add(name)
        if not subs:
            return f"CT logs found {len(data)} certs for {t} but no distinct subdomains."
        out = [f"=== Subdomain enum: {t} ===",
               f"Found {len(subs)} unique subdomains in CT logs ({len(data)} cert entries)."]
        for s in sorted(subs)[:50]:
            out.append(f"  {s}")
        if len(subs) > 50:
            out.append(f"  ... and {len(subs) - 50} more")
        out.append("")
        out.append("NOTE: CT-log enumeration is passive (no traffic to the target). Subdomains here may not currently resolve - use dns_audit on each to verify.")
        return "\n".join(out)
    except requests.Timeout:
        return "ERROR: crt.sh request timed out"
    except Exception as e:
        return f"subdomain_enum error: {e}"

def http_headers(url):
    """Fetch security-relevant HTTP headers and report on missing/weak ones.

    Checks: HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy,
    Permissions-Policy, server banner leakage.
    """
    # Normalize URL - default to https
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    # Extract host for allowlist check
    from urllib.parse import urlparse
    parsed = urlparse(u)
    host = parsed.hostname
    if not host:
        return f"ERROR: cannot parse host from {url!r}"
    ok, _ = _check_scan_target(host)
    if not ok:
        return f"REFUSED: {_}"
    import requests
    try:
        r = requests.get(u, timeout=15, allow_redirects=True,
                         headers={"User-Agent": "Clawdia/1.0 (personal security tool)"})
        hdrs = {k.lower(): v for k, v in r.headers.items()}
        summary = [f"=== HTTP headers: {u} ==="]
        summary.append(f"Status: {r.status_code} (after {len(r.history)} redirect(s))")
        if r.history:
            summary.append(f"Final URL: {r.url}")
        summary.append(f"Server: {hdrs.get('server', '(not disclosed)')}")
        findings = []
        # HSTS
        hsts = hdrs.get("strict-transport-security")
        if u.startswith("https://"):
            if not hsts:
                findings.append("HIGH: no Strict-Transport-Security (HSTS) header on HTTPS. Allows downgrade attacks. (CWE-319)")
            else:
                summary.append(f"HSTS: {hsts}")
                m = re.search(r"max-age=(\d+)", hsts)
                if m and int(m.group(1)) < 31536000:
                    findings.append(f"MEDIUM: HSTS max-age={m.group(1)} < 1 year. Increase to 31536000.")
                if "includesubdomains" not in hsts.lower():
                    findings.append("LOW: HSTS lacks includeSubDomains. Consider adding for subdomain protection.")
        # CSP
        csp = hdrs.get("content-security-policy")
        if not csp:
            findings.append("MEDIUM: no Content-Security-Policy. XSS protection weakened. (CWE-79)")
        else:
            summary.append(f"CSP: {csp[:150]}{'...' if len(csp) > 150 else ''}")
            if "unsafe-inline" in csp:
                findings.append("MEDIUM: CSP contains 'unsafe-inline' - reduces XSS protection.")
            if "unsafe-eval" in csp:
                findings.append("MEDIUM: CSP contains 'unsafe-eval' - allows eval()-based XSS.")
        # X-Frame-Options or frame-ancestors
        xfo = hdrs.get("x-frame-options")
        if not xfo and (not csp or "frame-ancestors" not in csp):
            findings.append("MEDIUM: no X-Frame-Options or CSP frame-ancestors. Clickjacking risk. (CWE-1021)")
        elif xfo:
            summary.append(f"X-Frame-Options: {xfo}")
        # X-Content-Type-Options
        xcto = hdrs.get("x-content-type-options")
        if xcto != "nosniff":
            findings.append("LOW: X-Content-Type-Options not 'nosniff'. MIME sniffing attacks possible. (CWE-451)")
        # Referrer-Policy
        rp = hdrs.get("referrer-policy")
        if not rp:
            findings.append("LOW: no Referrer-Policy header. Referrer leakage possible.")
        # Permissions-Policy
        pp = hdrs.get("permissions-policy")
        if not pp:
            findings.append("INFO: no Permissions-Policy header. Consider adding to restrict browser features.")
        # Server banner leakage
        srv = hdrs.get("server", "")
        if any(re.search(r"\d+\.\d+", srv) for srv in [srv] if srv):
            findings.append(f"LOW: Server header leaks version: {srv!r}. Reduces attacker recon effort. (CWE-200)")
        # X-Powered-By
        xpb = hdrs.get("x-powered-by")
        if xpb:
            findings.append(f"LOW: X-Powered-By header leaks tech stack: {xpb!r}. (CWE-200)")
        summary.append("")
        summary.append(f"--- Findings ({len(findings)}) ---")
        if not findings:
            summary.append("All major security headers present and well-configured.")
        else:
            for f in findings:
                summary.append(f"  {f}")
        return "\n".join(summary)
    except requests.Timeout:
        return f"ERROR: {u} request timed out"
    except requests.ConnectionError as e:
        return f"ERROR: cannot connect to {u}: {str(e)[:200]}"
    except Exception as e:
        return f"http_headers error: {e}"

def _parse_dmarc_record(record):
    """Parse a DMARC TXT record into a dict of tag=value pairs.

    Strips surrounding quotes (DNS TXT records often arrive quoted) and
    handles the standard tag=value;tag=value;... format from RFC 7489 sec 6.3.
    """
    s = record.strip().strip('"').strip()
    if not s.lower().startswith("v=dmarc1"):
        return None
    out = {}
    for pair in s.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, _, v = pair.partition("=")
        out[k.strip().lower()] = v.strip()
    return out

def dmarc_check(domain):
    """Look up and analyze the DMARC record for a domain.

    Returns: existing record (if any), parsed tag values, current adoption
    phase (monitor/quarantine/reject), and recommended next step.
    """
    ok, t = _check_scan_target(domain)
    if not ok:
        return f"REFUSED: {t}"
    try:
        import dns.resolver, dns.exception
    except ImportError:
        return "ERROR: dnspython not installed"

    fqdn = f"_dmarc.{t}"
    try:
        answers = dns.resolver.resolve(fqdn, "TXT", lifetime=5)
        txts = [str(r) for r in answers]
    except dns.resolver.NXDOMAIN:
        return (
            f"=== DMARC check: {t} ===\n"
            f"No DMARC record found at {fqdn}.\n\n"
            f"FINDING: HIGH severity. Without DMARC, receivers have no policy\n"
            f"  signal on whether to reject mail that fails SPF/DKIM. Anyone can\n"
            f"  spoof @{t} email addresses. (NIST SP 800-177, CWE-290)\n\n"
            f"NEXT STEP: Use dmarc_generate('{t}', 'monitor') to get a starter\n"
            f"  record. Add it as a TXT record at {fqdn} via your registrar."
        )
    except dns.resolver.NoAnswer:
        return f"{fqdn} exists but has no TXT records. No DMARC policy in effect."
    except dns.exception.DNSException as e:
        return f"DMARC lookup error: {e}"

    dmarc_txts = [t for t in txts if t.strip().strip('"').lower().startswith("v=dmarc1")]
    if not dmarc_txts:
        return (
            f"=== DMARC check: {t} ===\n"
            f"{fqdn} has TXT records but none are DMARC.\n"
            f"Records found: {txts}\n\n"
            f"FINDING: HIGH. Same effect as having no DMARC record."
        )
    if len(dmarc_txts) > 1:
        out_pre = (f"=== DMARC check: {t} ===\n"
                   f"WARNING: {len(dmarc_txts)} DMARC records found at {fqdn}.\n"
                   f"RFC 7489 says only ONE is allowed; receivers MAY ignore all\n"
                   f"of them in this case. Delete extras at your registrar.\n\n"
                   f"Records:\n")
        for rec in dmarc_txts:
            out_pre += f"  - {rec[:300]}\n"
        return out_pre

    record = dmarc_txts[0]
    parsed = _parse_dmarc_record(record)
    if not parsed:
        return f"Failed to parse DMARC record: {record}"

    # Determine phase from policy
    policy = parsed.get("p", "none").lower()
    sub_policy = parsed.get("sp", policy).lower()  # subdomain policy defaults to p
    pct = parsed.get("pct", "100")
    try:
        pct_int = int(pct)
    except ValueError:
        pct_int = 100

    if policy == "none":
        phase = "MONITOR (p=none)"
        phase_meaning = "No enforcement. Receivers report failures but still deliver mail."
        next_step = "After 2-4 weeks of aggregate reports confirm legitimate senders, advance to dmarc_generate(domain, 'quarantine')."
    elif policy == "quarantine":
        if pct_int < 100:
            phase = f"QUARANTINE (p=quarantine, pct={pct_int}%)"
            phase_meaning = f"Soft enforcement on {pct_int}% of failing mail (sent to spam). Ramp pct up gradually."
            if pct_int < 25:
                next_step = "Ramp pct to 25, then 50, then 100 over weeks. Watch reports for legitimate senders failing."
            elif pct_int < 100:
                next_step = "Ramp pct toward 100. Once stable at pct=100, advance to dmarc_generate(domain, 'reject')."
            else:
                next_step = "(handled above)"
        else:
            phase = "QUARANTINE (p=quarantine, pct=100)"
            phase_meaning = "Full soft enforcement. Failing mail goes to spam."
            next_step = "Once stable for several weeks, advance to dmarc_generate(domain, 'reject') for full enforcement."
    elif policy == "reject":
        phase = "REJECT (p=reject)"
        phase_meaning = "Full enforcement. Failing mail is rejected outright."
        next_step = "You're at the strongest policy. Verify sp= tag matches your subdomain mail strategy."
    else:
        phase = f"UNKNOWN (p={policy!r})"
        phase_meaning = "Policy value not recognized."
        next_step = "Review the record for typos."

    # Build findings
    findings = []
    rua = parsed.get("rua", "")
    if not rua:
        findings.append("MEDIUM: No rua= tag (aggregate report address). You'll get no visibility into who is sending mail as you.")
    ruf = parsed.get("ruf", "")
    aspf = parsed.get("aspf", "r").lower()  # default per RFC
    adkim = parsed.get("adkim", "r").lower()
    if policy == "none" and not rua:
        findings.append("HIGH: monitor mode (p=none) is useless without rua= - you can't tell if real senders are failing.")
    if sub_policy != policy and parsed.get("sp"):
        findings.append(f"INFO: subdomain policy sp={sub_policy} differs from main policy p={policy}. Confirm this is intentional.")

    out = [
        f"=== DMARC check: {t} ===",
        f"Record at {fqdn}:",
        f"  {record.strip(chr(34))}",
        "",
        f"Phase: {phase}",
        f"Meaning: {phase_meaning}",
        "",
        f"Parsed tags:",
    ]
    for k in ("v", "p", "sp", "pct", "rua", "ruf", "aspf", "adkim", "fo"):
        if k in parsed:
            out.append(f"  {k} = {parsed[k]}")
    out.append("")
    if findings:
        out.append(f"--- Findings ({len(findings)}) ---")
        for f in findings:
            out.append(f"  {f}")
        out.append("")
    out.append(f"NEXT STEP: {next_step}")
    return "\n".join(out)

def dmarc_generate(domain, phase="monitor", report_email=None):
    """Generate a recommended DMARC TXT record for adoption phase.

    Args:
      domain: domain you own (must be on scan allowlist).
      phase: one of "monitor" (p=none, start here), "quarantine" (mid-stage),
             or "reject" (full enforcement, last stage).
      report_email: where DMARC aggregate reports go. Defaults to
                    dmarc-reports@<domain>.

    Returns the TXT record string + the FQDN to add it at + adoption guidance.
    """
    ok, t = _check_scan_target(domain)
    if not ok:
        return f"REFUSED: {t}"

    phase_norm = (phase or "monitor").strip().lower()
    if phase_norm not in ("monitor", "quarantine", "reject"):
        return (f"ERROR: phase must be one of: monitor, quarantine, reject.\n"
                f"Got: {phase!r}.\n\n"
                f"Adoption order: monitor -> quarantine -> reject. Start with monitor\n"
                f"unless you already have a DMARC record (use dmarc_check first).")

    if not report_email:
        report_email = f"dmarc-reports@{t}"
    if "@" not in report_email:
        return f"ERROR: report_email must be a valid email address. Got: {report_email!r}"

    if phase_norm == "monitor":
        record = f"v=DMARC1; p=none; rua=mailto:{report_email}; pct=100; aspf=r; adkim=r; fo=1"
        rationale = (
            "MONITOR PHASE (p=none): No enforcement. Receivers will send you\n"
            "  aggregate reports about who is sending mail claiming to be from\n"
            f"  @{t}, but they will NOT reject failing mail. Run this for 2-4\n"
            "  weeks to confirm your legitimate senders (your mail provider,\n"
            "  marketing tools, etc.) are all SPF/DKIM-aligned BEFORE advancing\n"
            "  to quarantine. Without this monitoring period, the next phase will\n"
            "  silently send your real mail to spam.\n"
        )
        next_advice = (
            "After 2-4 weeks of clean reports: dmarc_generate(domain, 'quarantine')."
        )
    elif phase_norm == "quarantine":
        # Conservative start at 10% then ramp
        record = f"v=DMARC1; p=quarantine; sp=quarantine; rua=mailto:{report_email}; pct=10; aspf=r; adkim=r; fo=1"
        rationale = (
            "QUARANTINE PHASE (p=quarantine, pct=10): Soft enforcement on 10% of\n"
            "  failing mail (goes to spam). Start small and ramp up - watch your\n"
            "  aggregate reports and your own inbox for legitimate mail going to\n"
            "  spam. Pattern: 10% -> 25% -> 50% -> 100% over several weeks each.\n"
            "  After steady-state at 100%, advance to reject.\n"
        )
        next_advice = (
            "Increment pct=10 to pct=25 next week, pct=50 the week after, then\n"
            "  pct=100. Once stable: dmarc_generate(domain, 'reject')."
        )
    else:  # reject
        record = f"v=DMARC1; p=reject; sp=reject; rua=mailto:{report_email}; pct=100; aspf=r; adkim=r; fo=1"
        rationale = (
            "REJECT PHASE (p=reject): Full enforcement. Failing mail will be\n"
            "  rejected outright by receivers. This is the strongest DMARC\n"
            "  policy and should ONLY be reached after weeks at p=quarantine\n"
            f"  pct=100 with no legitimate senders failing for @{t}.\n"
        )
        next_advice = "You're at the strongest policy. Monitor aggregate reports indefinitely."

    return (
        f"=== DMARC record for {t} ===\n\n"
        f"Add this as a TXT record:\n\n"
        f"  Host/Name: _dmarc.{t}\n"
        f"  Type:      TXT\n"
        f"  Value:     {record}\n"
        f"  TTL:       3600 (or your registrar's default)\n\n"
        f"{rationale}\n"
        f"REPORTING: aggregate reports will be sent to {report_email}.\n"
        f"You will receive XML files daily from each major receiver (Google,\n"
        f"Microsoft, Yahoo, etc.). Free DMARC report parsers: dmarcian.com,\n"
        f"valimail.com, or self-hosted parsedmarc.\n\n"
        f"NEXT: {next_advice}\n\n"
        f"VERIFY: After adding the record (DNS propagation typically takes\n"
        f"  5-30 minutes), run dmarc_check('{t}') to confirm receivers can see it."
    )

def _spf_count_lookups(spf_record):
    """Count the number of DNS lookups an SPF record would require.

    Per RFC 7208 sec 4.6.4, each of these terms counts as a lookup:
      include, a, mx, exists, redirect, ptr
    Plus a maximum of 10 total lookups including recursive ones.
    This counter only sees the top-level record; nested includes add more.
    """
    s = spf_record.lower()
    count = 0
    count += len(re.findall(r"\b(include|exists|redirect)[:=]", s))
    # a and mx without colon also count
    for token in s.split():
        if token in ("a", "mx", "ptr"):
            count += 1
        elif token.startswith(("a:", "mx:", "ptr:")):
            count += 1
        elif token.startswith(("+a:", "+mx:", "?a:", "?mx:", "~a:", "~mx:", "-a:", "-mx:")):
            count += 1
    return count

def spf_check(domain):
    """Look up and analyze the existing SPF record for a domain.

    Returns the record, the all-qualifier (softfail/hardfail/neutral/pass),
    a DNS-lookup count estimate, and findings.
    """
    ok, t = _check_scan_target(domain)
    if not ok:
        return f"REFUSED: {t}"
    try:
        import dns.resolver, dns.exception
    except ImportError:
        return "ERROR: dnspython not installed"

    try:
        answers = dns.resolver.resolve(t, "TXT", lifetime=5)
        txts = [str(r) for r in answers]
    except dns.resolver.NXDOMAIN:
        return f"NXDOMAIN: {t} does not exist."
    except dns.resolver.NoAnswer:
        return f"{t} has no TXT records. No SPF policy in effect.\n\nFINDING: HIGH. Mail spoofing is unrestricted. Use spf_generate to produce a starter record."
    except dns.exception.DNSException as e:
        return f"SPF lookup error: {e}"

    spf_recs = [r for r in txts if r.strip().strip(chr(34)).lower().startswith("v=spf1")]
    if not spf_recs:
        return (f"=== SPF check: {t} ===\n"
                f"No SPF record found among {len(txts)} TXT record(s).\n\n"
                f"FINDING: HIGH. Mail from unauthorized servers cannot be policy-rejected by receivers. (CWE-290)")

    if len(spf_recs) > 1:
        return (f"=== SPF check: {t} ===\n"
                f"WARNING: {len(spf_recs)} SPF records found at {t}.\n"
                f"RFC 7208 sec 3.2: receivers MUST treat the domain as having no SPF when multiple records exist.\n"
                f"This means your SPF is effectively BROKEN. Delete extras at your registrar.")

    record = spf_recs[0].strip().strip(chr(34))
    findings = []
    # Determine all qualifier
    qualifier = None
    for term in ("-all", "~all", "?all", "+all"):
        if term in record.lower():
            qualifier = term
            break
    if not qualifier:
        findings.append("MEDIUM: SPF record has no 'all' terminator. Receivers treat this as neutral. Add -all or ~all.")
        phase = "UNKNOWN (no all qualifier)"
    elif qualifier == "+all":
        findings.append("CRITICAL: SPF uses +all - allows ANY server to send mail as you. (CWE-290) Change immediately.")
        phase = "PASS (+all - WIDE OPEN)"
    elif qualifier == "?all":
        findings.append("HIGH: SPF uses ?all (neutral). Receivers get no signal on what to do with failing mail.")
        phase = "NEUTRAL (?all)"
    elif qualifier == "~all":
        findings.append("MEDIUM: SPF uses ~all (softfail). Receivers may still accept spoofed mail. Consider -all (hardfail). (CIS Control 9.4)")
        phase = "SOFTFAIL (~all)"
    elif qualifier == "-all":
        phase = "HARDFAIL (-all)"

    # Lookup count
    lookups = _spf_count_lookups(record)
    if lookups > 10:
        findings.append(f"CRITICAL: top-level SPF has {lookups} DNS-lookup terms (RFC 7208 max is 10). Record is BROKEN as-is.")
    elif lookups >= 8:
        findings.append(f"MEDIUM: top-level SPF has {lookups} DNS-lookup terms (max 10). Nested includes may push over. Consider SPF flattening.")

    # PTR check
    if "ptr" in record.lower().split():
        findings.append("MEDIUM: SPF uses ptr mechanism. RFC 7208 strongly discourages this - slow lookups, security issues. Remove.")

    out = [
        f"=== SPF check: {t} ===",
        f"Record:",
        f"  {record}",
        "",
        f"Phase: {phase}",
        f"Top-level DNS lookups: {lookups} / 10 allowed by RFC 7208",
    ]
    # Find included senders
    includes = re.findall(r"\binclude:([^\s]+)", record)
    if includes:
        out.append("")
        out.append(f"Authorized senders (via include):")
        for inc in includes:
            # Reverse-lookup to provider name
            provider_name = next((k for k, v in _SPF_PROVIDER_INCLUDES.items() if v == f"include:{inc}"), None)
            label = f" ({provider_name})" if provider_name else ""
            out.append(f"  {inc}{label}")
    ip4s = re.findall(r"ip4:([0-9./]+)", record)
    ip6s = re.findall(r"ip6:([0-9a-fA-F:/]+)", record)
    if ip4s or ip6s:
        out.append("")
        out.append("Authorized IPs:")
        for ip in ip4s:
            out.append(f"  ip4:{ip}")
        for ip in ip6s:
            out.append(f"  ip6:{ip}")

    out.append("")
    if findings:
        out.append(f"--- Findings ({len(findings)}) ---")
        for f in findings:
            out.append(f"  {f}")
    else:
        out.append("--- Findings (0) ---")
        out.append("  No issues detected. SPF is solid.")
    return "\n".join(out)

def spf_generate(domain, senders=None, qualifier="softfail"):
    """Generate a recommended SPF record from a list of senders + qualifier.

    Args:
      domain: domain you own (must be on scan allowlist).
      senders: list/string of provider names from _SPF_PROVIDER_INCLUDES, or
               raw include strings (e.g. "include:spf.mycorp.com" or
               "ip4:1.2.3.0/24"). Comma-separated string also accepted.
               If None, returns the list of known providers.
      qualifier: "hardfail" (-all, strict), "softfail" (~all, lenient),
                 or "neutral" (?all, monitor only).

    Returns record + the FQDN to add it at + adoption guidance.
    """
    ok, t = _check_scan_target(domain)
    if not ok:
        return f"REFUSED: {t}"

    if senders is None or (isinstance(senders, str) and not senders.strip()):
        provider_list = sorted(_SPF_PROVIDER_INCLUDES.keys())
        return (f"=== Available SPF sender names ===\n\n"
                f"Pass any combination of these to spf_generate. You can also pass raw\n"
                f"include strings like 'include:spf.mycorp.com' or 'ip4:1.2.3.0/24'.\n\n"
                f"Known providers:\n  " + ", ".join(provider_list) + "\n\n"
                f"Examples:\n"
                f"  spf_generate('{t}', 'easywp')\n"
                f"  spf_generate('{t}', ['google', 'mailchimp'], 'softfail')\n"
                f"  spf_generate('{t}', 'google, sendgrid, mailgun', 'hardfail')")

    # Normalize senders to a list
    if isinstance(senders, str):
        sender_list = [s.strip().lower() for s in senders.split(",") if s.strip()]
    else:
        sender_list = [str(s).strip().lower() for s in senders if str(s).strip()]

    # Resolve each to a directive
    directives = []
    unknown = []
    for s in sender_list:
        if s in _SPF_PROVIDER_INCLUDES:
            d = _SPF_PROVIDER_INCLUDES[s]
            if d not in directives:
                directives.append(d)
        elif s.startswith(("include:", "ip4:", "ip6:", "a:", "mx:", "exists:")) or s in ("a", "mx"):
            if s not in directives:
                directives.append(s)
        else:
            unknown.append(s)

    if unknown:
        return (f"ERROR: unknown sender names: {unknown}\n\n"
                f"Pass spf_generate('{t}') with no senders to list known providers.\n"
                f"Or pass raw directives like 'include:spf.mycorp.com', 'ip4:1.2.3.0/24'.")

    if not directives:
        return f"ERROR: no valid senders specified. Pass at least one."

    # Resolve qualifier
    qmap = {
        "hardfail": "-all", "reject": "-all", "strict": "-all", "-all": "-all",
        "softfail": "~all", "soft": "~all", "~all": "~all",
        "neutral": "?all", "monitor": "?all", "?all": "?all",
    }
    qnorm = (qualifier or "softfail").strip().lower()
    if qnorm not in qmap:
        return (f"ERROR: qualifier must be one of: hardfail, softfail, neutral.\n"
                f"Got: {qualifier!r}.\n\n"
                f"  hardfail = -all = STRICT, mail rejected (use ONLY after weeks at softfail with clean reports)\n"
                f"  softfail = ~all = LENIENT (recommended starting point)\n"
                f"  neutral  = ?all = NO POLICY (avoid - same effect as no SPF)")
    all_qual = qmap[qnorm]

    record = "v=spf1 " + " ".join(directives) + " " + all_qual

    # Lookup-count check
    lookups = _spf_count_lookups(record)
    lookup_warning = ""
    if lookups > 10:
        lookup_warning = f"\n\nWARNING: This record has {lookups} top-level DNS-lookup terms. RFC 7208 cap is 10. Receivers will treat the record as PermError (broken). Consider SPF flattening or removing senders."
    elif lookups >= 8:
        lookup_warning = f"\n\nNOTE: This record has {lookups} top-level DNS-lookup terms (max 10). Nested includes inside providers may push you over. Test with spf_check after adding."

    # Qualifier rationale
    if all_qual == "-all":
        qrat = ("HARDFAIL (-all): Receivers MUST reject mail from unauthorized senders.\n"
                "  Use ONLY after running ~all (softfail) for weeks and confirming\n"
                "  via DMARC aggregate reports that NO legitimate senders are failing.\n"
                "  Going to -all without verification will silently break real mail.")
        next_advice = "You're at the strictest SPF policy. Pair with DMARC p=reject."
    elif all_qual == "~all":
        qrat = ("SOFTFAIL (~all): Receivers may still deliver mail from unauthorized\n"
                "  senders but should mark it suspicious. Recommended starting point\n"
                "  for any SPF rollout. Watch DMARC aggregate reports for 2-4 weeks\n"
                "  to confirm all your real senders are listed before tightening.")
        next_advice = f"After DMARC reports confirm clean alignment: spf_generate('{t}', senders, 'hardfail')."
    else:  # ?all
        qrat = ("NEUTRAL (?all): No policy signal. Equivalent to having no SPF.\n"
                "  ONLY use this temporarily during initial deployment if you're\n"
                "  unsure about your sender list. Move to ~all as soon as possible.")
        next_advice = f"Move to softfail as soon as you've confirmed your sender list: spf_generate('{t}', senders, 'softfail')."

    return (
        f"=== SPF record for {t} ===\n\n"
        f"Add this as a TXT record AT THE ROOT of the domain (replace any existing v=spf1 record):\n\n"
        f"  Host/Name: @  (or leave blank for root domain)\n"
        f"  Type:      TXT\n"
        f"  Value:     {record}\n"
        f"  TTL:       3600 (or your registrar's default)\n\n"
        f"{qrat}\n"
        f"DNS lookups: {lookups} / 10 (RFC 7208 max)"
        f"{lookup_warning}\n\n"
        f"AUTHORIZED SENDERS in this record:\n"
        + "".join(f"  - {d}\n" for d in directives) +
        f"\nIMPORTANT: There can only be ONE v=spf1 record per domain. If a record\n"
        f"  already exists, EDIT it - do not add a second one. Run spf_check('{t}')\n"
        f"  first to see the current record.\n\n"
        f"NEXT: {next_advice}\n\n"
        f"VERIFY: After adding (5-30 min DNS propagation), run spf_check('{t}') to\n"
        f"  confirm the record is live and parses cleanly."
    )

def _parse_dkim_record(record):
    """Parse a DKIM TXT record into a dict of tag=value pairs.

    DKIM records can be split across multiple quoted strings in DNS responses;
    we strip quotes and concatenate before parsing.
    """
    # DNS TXT can come back as concatenated quoted segments like:
    #   "v=DKIM1; k=rsa; " "p=MIGfMA0..."
    s = re.sub(r'"\s*"', "", record).strip().strip('"').strip()
    if not s.lower().startswith("v=dkim1"):
        return None
    out = {}
    for pair in s.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, _, v = pair.partition("=")
        out[k.strip().lower()] = v.strip()
    return out

def dkim_check(domain, selectors=None):
    """Check DKIM records for a domain.

    Args:
      domain: must be on scan allowlist
      selectors: comma-separated string, list, or None.
                 If None, probes a list of common selectors.

    Returns the records found, parsed tags, key size for RSA, and findings.
    """
    ok, t = _check_scan_target(domain)
    if not ok:
        return f"REFUSED: {t}"
    try:
        import dns.resolver, dns.exception
    except ImportError:
        return "ERROR: dnspython not installed"

    if selectors is None or (isinstance(selectors, str) and not selectors.strip()):
        sel_list = _DKIM_COMMON_SELECTORS
        probing_common = True
    else:
        if isinstance(selectors, str):
            sel_list = [s.strip() for s in selectors.split(",") if s.strip()]
        else:
            sel_list = [str(s).strip() for s in selectors if str(s).strip()]
        probing_common = False

    found = []
    not_found = []
    errors = []
    for sel in sel_list:
        fqdn = f"{sel}._domainkey.{t}"
        try:
            ans = dns.resolver.resolve(fqdn, "TXT", lifetime=5)
            txts = [str(r) for r in ans]
            for raw in txts:
                parsed = _parse_dkim_record(raw)
                if parsed:
                    found.append((sel, fqdn, raw, parsed))
                    break
            else:
                errors.append((fqdn, "TXT record exists but is not a DKIM v=DKIM1 record"))
        except dns.resolver.NXDOMAIN:
            not_found.append(sel)
        except dns.resolver.NoAnswer:
            not_found.append(sel)
        except dns.exception.DNSException as e:
            errors.append((fqdn, f"DNS error: {e}"))

    out = [f"=== DKIM check: {t} ==="]
    if probing_common:
        out.append(f"Probed {len(sel_list)} common selectors.")
    else:
        out.append(f"Probed {len(sel_list)} selectors specified.")
    out.append("")

    findings = []
    if not found:
        out.append("No DKIM records found at any probed selector.")
        if probing_common:
            out.append("")
            out.append("This may mean: (a) DKIM is not configured, or (b) you use a custom")
            out.append("selector name not in the common probe list. Check your mail provider's")
            out.append("DNS docs for the actual selector name and pass it via selectors= arg.")
        findings.append("HIGH: no DKIM record found. Mail receivers cannot verify message origin via cryptographic signature. Pair with DMARC for spoofing protection. (RFC 6376, NIST SP 800-177)")
    else:
        for sel, fqdn, raw, parsed in found:
            out.append(f"--- {fqdn} ---")
            out.append(f"  Record: {raw[:250]}{'...' if len(raw) > 250 else ''}")
            for tag in ("v", "k", "h", "s", "t"):
                if tag in parsed:
                    out.append(f"  {tag}={parsed[tag]}")
            p = parsed.get("p", "")
            if not p:
                findings.append(f"HIGH: DKIM at {fqdn} has empty p= (REVOKED key). All mail signed with this selector will fail verification.")
            else:
                out.append(f"  p={p[:60]}... ({len(p)} chars total)")
                # Estimate key bits from base64 p= length
                # Rough heuristic: RSA-1024 = ~216 chars, RSA-2048 = ~392 chars
                if len(p) < 200:
                    findings.append(f"HIGH: DKIM at {fqdn} likely uses RSA-1024 or weaker (key blob {len(p)} chars). RFC 8301 says 1024-bit is insufficient; use 2048+. (CWE-326)")
                elif len(p) < 380:
                    findings.append(f"MEDIUM: DKIM at {fqdn} key blob is {len(p)} chars - may be smaller than recommended 2048-bit. Verify with mail provider.")
            if parsed.get("t", "").lower() == "y":
                findings.append(f"MEDIUM: DKIM at {fqdn} has t=y (TEST MODE). Receivers may ignore failures. Remove for production.")
            if parsed.get("k", "rsa").lower() not in ("rsa", "ed25519"):
                findings.append(f"INFO: DKIM at {fqdn} uses key type k={parsed.get('k')} - unusual. RFC 6376 standard is rsa or ed25519.")
            out.append("")

    if not_found and probing_common:
        out.append(f"Selectors with no record: {', '.join(not_found[:10])}{', ...' if len(not_found) > 10 else ''}")
        out.append("")
    if errors:
        out.append(f"--- Errors ({len(errors)}) ---")
        for fqdn, err in errors[:5]:
            out.append(f"  {fqdn}: {err}")
        out.append("")

    out.append(f"--- Findings ({len(findings)}) ---")
    if not findings:
        out.append("  No issues detected. DKIM looks healthy.")
    else:
        for f in findings:
            out.append(f"  {f}")
    return "\n".join(out)

def tls_audit(host_or_port):
    """Deep TLS posture audit: protocol versions accepted, full cert chain,
    weak cipher detection, HSTS configuration.

    Goes deeper than cert_check by probing TLS 1.0/1.1/1.2/1.3 separately
    and checking what cipher categories the server accepts.
    """
    # Parse host:port
    target_in = host_or_port.strip()
    if ":" in target_in and not target_in.startswith("["):
        host, _, p = target_in.rpartition(":")
        try:
            port = int(p)
        except ValueError:
            host = target_in
            port = 443
    else:
        host = target_in
        port = 443

    ok, t = _check_scan_target(host)
    if not ok:
        return f"REFUSED: {t}"

    try:
        import ssl, socket
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives.asymmetric import rsa, ec
    except ImportError as e:
        return f"ERROR: missing deps: {e}"

    out = [f"=== TLS deep audit: {t}:{port} ==="]
    findings = []

    # Probe each TLS protocol version separately
    # Per Python docs: PROTOCOL_TLS_CLIENT auto-negotiates; for specific version
    # we use min/max version constraints on a context.
    protocol_results = {}
    protocols_to_test = [
        ("TLSv1.0", ssl.TLSVersion.TLSv1, ssl.TLSVersion.TLSv1),
        ("TLSv1.1", ssl.TLSVersion.TLSv1_1, ssl.TLSVersion.TLSv1_1),
        ("TLSv1.2", ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_2),
        ("TLSv1.3", ssl.TLSVersion.TLSv1_3, ssl.TLSVersion.TLSv1_3),
    ]
    for name, min_v, max_v in protocols_to_test:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.minimum_version = min_v
            ctx.maximum_version = max_v
        except (ValueError, AttributeError, ssl.SSLError) as e:
            # Some old protocol versions may be disabled at OpenSSL build level
            protocol_results[name] = f"unavailable (client doesn't support: {e})"
            continue
        try:
            with socket.create_connection((t, port), timeout=8) as sock:
                with ctx.wrap_socket(sock, server_hostname=t) as ssock:
                    cipher = ssock.cipher()
                    protocol_results[name] = f"ACCEPTED (cipher: {cipher[0]})"
        except ssl.SSLError as e:
            protocol_results[name] = f"rejected ({type(e).__name__})"
        except socket.timeout:
            protocol_results[name] = "timeout"
        except (ConnectionRefusedError, OSError) as e:
            return f"ERROR: cannot connect to {t}:{port} - {e}"
        except Exception as e:
            protocol_results[name] = f"error: {type(e).__name__}: {e}"

    out.append("")
    out.append("Protocol version support:")
    for name, status in protocol_results.items():
        out.append(f"  {name}: {status}")
    if "ACCEPTED" in protocol_results.get("TLSv1.0", ""):
        findings.append("HIGH: TLS 1.0 accepted. Deprecated by RFC 8996 (2021). Disable. (CWE-326, CIS Control 4.10)")
    if "ACCEPTED" in protocol_results.get("TLSv1.1", ""):
        findings.append("HIGH: TLS 1.1 accepted. Deprecated by RFC 8996 (2021). Disable. (CWE-326)")
    if "ACCEPTED" not in protocol_results.get("TLSv1.2", "") and "ACCEPTED" not in protocol_results.get("TLSv1.3", ""):
        findings.append("CRITICAL: Neither TLS 1.2 nor TLS 1.3 accepted. Service is unreachable for modern clients.")
    if "ACCEPTED" not in protocol_results.get("TLSv1.3", ""):
        findings.append("MEDIUM: TLS 1.3 not accepted. Modern best practice. (RFC 8446)")

    # Full cert chain via a single default-context connection
    out.append("")
    out.append("Certificate chain:")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((t, port), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=t) as ssock:
                # getpeercert(True) gives only the leaf; we need _SSLSocket._sslobj internal
                # Workaround: use get_verified_chain if available (Python 3.10+)
                try:
                    chain_ders = ssock.get_verified_chain()
                except (AttributeError, ssl.SSLError):
                    try:
                        chain_ders = ssock.get_unverified_chain()
                    except AttributeError:
                        chain_ders = [ssock.getpeercert(binary_form=True)]
                negotiated = ssock.version()
                cipher = ssock.cipher()
                out.append(f"  Negotiated protocol: {negotiated}")
                out.append(f"  Negotiated cipher: {cipher[0]} ({cipher[2]}-bit)")
                # Forward secrecy check via cipher name
                cipher_name = cipher[0]
                fs_indicators = ("ECDHE", "DHE", "X25519", "TLS_AES", "TLS_CHACHA")
                if not any(ind in cipher_name for ind in fs_indicators):
                    findings.append(f"HIGH: cipher {cipher_name} does not provide forward secrecy. (CWE-326)")
                if "CBC" in cipher_name:
                    findings.append(f"MEDIUM: cipher {cipher_name} uses CBC mode (vulnerable to Lucky13, BEAST historically). Prefer AEAD ciphers like GCM or ChaCha20.")
                if any(weak in cipher_name for weak in ("RC4", "3DES", "DES", "MD5", "NULL", "EXPORT")):
                    findings.append(f"CRITICAL: cipher {cipher_name} is broken/weak. (CWE-327)")
    except Exception as e:
        return f"ERROR during chain fetch: {e}"

    # Parse each cert in chain
    out.append(f"  Chain length: {len(chain_ders)}")
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    for i, der in enumerate(chain_ders):
        try:
            if isinstance(der, bytes):
                cert = x509.load_der_x509_certificate(der)
            else:
                # get_verified_chain returns bytes in Python 3.13; older versions may
                # return _ssl.Certificate. Try to coerce.
                cert = x509.load_der_x509_certificate(bytes(der))
            cn = next((a.value for a in cert.subject if a.oid == NameOID.COMMON_NAME), "(none)")
            issuer_cn = next((a.value for a in cert.issuer if a.oid == NameOID.COMMON_NAME), "(none)")
            try:
                nva = cert.not_valid_after_utc
            except AttributeError:
                nva = cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
            days = (nva - now).days
            level = "leaf" if i == 0 else f"intermediate-{i}" if i < len(chain_ders) - 1 else "root"
            out.append(f"  [{i}] {level}: CN={cn}")
            out.append(f"      issuer={issuer_cn}, expires={nva.date()} ({days} days)")
            if days < 0:
                findings.append(f"CRITICAL: chain cert [{i}] ({cn}) EXPIRED {-days} days ago.")
            elif days < 30 and i == 0:
                findings.append(f"MEDIUM: leaf certificate expires in {days} days.")
            sig_algo = cert.signature_hash_algorithm.name if cert.signature_hash_algorithm else "?"
            if sig_algo in ("md5", "sha1"):
                findings.append(f"HIGH: chain cert [{i}] uses weak signature algorithm {sig_algo}. (CWE-327)")
            pk = cert.public_key()
            if isinstance(pk, rsa.RSAPublicKey):
                if pk.key_size < 2048:
                    findings.append(f"HIGH: chain cert [{i}] uses RSA-{pk.key_size} (< 2048 bits). (CWE-326)")
        except Exception as e:
            out.append(f"  [{i}] parse error: {e}")

    # HSTS preload status (best-effort - the canonical preload list is at the HSTS preload site;
    # for a quick check we just look at the cert + HTTP response)
    out.append("")
    out.append("HSTS:")
    try:
        import requests
        r = requests.get(f"https://{t}:{port}/", timeout=8, allow_redirects=False,
                         headers={"User-Agent": "Clawdia/1.0"})
        hsts = r.headers.get("Strict-Transport-Security")
        if not hsts:
            findings.append("HIGH: no HSTS header on HTTPS response. (CWE-319)")
            out.append("  No HSTS header in response.")
        else:
            out.append(f"  {hsts}")
            m = re.search(r"max-age=(\d+)", hsts)
            if m:
                age = int(m.group(1))
                if age < 31536000:
                    findings.append(f"MEDIUM: HSTS max-age={age} < 1 year (31536000). Browsers may not honor preload.")
            if "preload" in hsts.lower() and "includesubdomains" not in hsts.lower():
                findings.append("MEDIUM: HSTS has preload directive but missing includeSubDomains. Preload submission will be rejected.")
    except Exception as e:
        out.append(f"  HSTS check failed: {e}")

    out.append("")
    out.append(f"--- Findings ({len(findings)}) ---")
    if not findings:
        out.append("  No issues detected. TLS posture is solid.")
    else:
        for f in findings:
            out.append(f"  {f}")
    return "\n".join(out)


# ---- Tool schemas ----
SCHEMAS = [
    {"name":"epss_lookup","description":"Look up the EPSS (Exploit Prediction Scoring System) score for a CVE from FIRST.org. Returns the 0-1 probability that the CVE will be exploited in the wild within the next 30 days, plus its percentile among all CVEs. Use when prioritizing patches or when Sean asks how likely a specific CVE is to be exploited. Accepts CVE-YYYY-NNNN format (case insensitive).","input_schema":{"type":"object","properties":{"cve_id":{"type":"string","description":"CVE identifier, e.g. CVE-2024-3094"}},"required":["cve_id"]}},
    {"name":"kev_check","description":"Check whether a CVE is on CISA's Known Exploited Vulnerabilities (KEV) catalog. KEV entries are CVEs confirmed exploited in the wild; under BOD 22-01 they have a federal remediation deadline. Returns vendor, product, date added, due date, and ransomware involvement. Catalog is cached locally 24h.","input_schema":{"type":"object","properties":{"cve_id":{"type":"string","description":"CVE identifier, e.g. CVE-2024-3094"}},"required":["cve_id"]}},
    {"name":"cve_lookup","description":"Look up the full CVE record from NVD (NIST National Vulnerability Database): description, CVSS v3.1 score and vector, CWE weakness classifications, published/modified dates, and reference URLs. Use when Sean wants technical details about a specific CVE.","input_schema":{"type":"object","properties":{"cve_id":{"type":"string","description":"CVE identifier, e.g. CVE-2024-3094"}},"required":["cve_id"]}},
    {"name":"cve_enrich","description":"One-call combined CVE enrichment: NVD details + EPSS exploitation probability + CISA KEV status + plain-English priority guidance based on industry-common patching rules (KEV/EPSS/CVSS combination). This is the right tool when Sean asks general questions like 'tell me about CVE-X', 'how bad is CVE-X', or 'should I worry about CVE-X'. Use the individual epss_lookup/kev_check/cve_lookup tools only when Sean specifically wants one of those data sources.","input_schema":{"type":"object","properties":{"cve_id":{"type":"string","description":"CVE identifier, e.g. CVE-2024-3094"}},"required":["cve_id"]}},
    {"name":"dns_audit","description":"Audit DNS hygiene for a domain Sean owns: SPF, DMARC, MTA-STS, MX, NS, basic records. Returns findings prioritized by severity (e.g. missing SPF = HIGH). Target MUST be on the scan allowlist at /etc/clawdia/scan_allowlist.json. Refuses domains Sean does not own.","input_schema":{"type":"object","properties":{"domain":{"type":"string","description":"Domain to audit, e.g. hollowed-ground.com"}},"required":["domain"]}},
    {"name":"cert_check","description":"Inspect the TLS certificate and config for a host (default port 443, or use host:port). Returns subject, issuer, SANs, expiry days, signature algorithm, TLS version, cipher, plus findings (expired cert, weak crypto, hostname mismatch). Target MUST be on the scan allowlist.","input_schema":{"type":"object","properties":{"host":{"type":"string","description":"Host to check (optionally host:port), e.g. hollowed-ground.com or hollowed-ground.com:443"}},"required":["host"]}},
    {"name":"subdomain_enum","description":"Passive subdomain discovery for a domain via Certificate Transparency logs (crt.sh). Generates no traffic to the target itself - queries a public CT log aggregator. Useful for finding subdomains you forgot you had. Target MUST be on the scan allowlist.","input_schema":{"type":"object","properties":{"domain":{"type":"string","description":"Root domain, e.g. hollowed-ground.com"}},"required":["domain"]}},
    {"name":"http_headers","description":"Fetch and analyze security-relevant HTTP response headers from a URL: HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, plus server banner/tech leakage. Returns findings by severity. Target host MUST be on the scan allowlist.","input_schema":{"type":"object","properties":{"url":{"type":"string","description":"URL to check, e.g. https://hollowed-ground.com or just hollowed-ground.com (defaults to https)"}},"required":["url"]}},
    {"name":"dmarc_check","description":"Look up and analyze the existing DMARC record for a domain Sean owns. Parses the record, identifies the adoption phase (monitor/quarantine/reject), and recommends the next step in the rollout. Use this to verify a record after adding it, or to understand what an existing record actually does. Target MUST be on the scan allowlist.","input_schema":{"type":"object","properties":{"domain":{"type":"string","description":"Domain to check, e.g. hollowed-ground.com"}},"required":["domain"]}},
    {"name":"dmarc_generate","description":"Generate a recommended DMARC TXT record for a domain Sean owns, sized to the adoption phase. Phase is one of: monitor (p=none, START HERE if no record exists), quarantine (mid-stage, soft enforcement at increasing pct), reject (full enforcement, FINAL stage only after weeks at quarantine pct=100). Returns the exact record string, the FQDN to add it at, adoption guidance, and the next step. This tool does NOT write DNS - it only generates the recommended record for Sean to add at his registrar manually. Target MUST be on the scan allowlist.","input_schema":{"type":"object","properties":{"domain":{"type":"string","description":"Domain to generate record for"},"phase":{"type":"string","enum":["monitor","quarantine","reject"],"default":"monitor","description":"Adoption phase: start with monitor unless a record already exists"},"report_email":{"type":"string","description":"Where DMARC aggregate reports should be sent. Defaults to dmarc-reports@<domain>"}},"required":["domain"]}},
    {"name":"spf_check","description":"Look up and analyze the existing SPF record for a domain Sean owns. Reports the all-qualifier (hardfail/softfail/neutral/pass), counts DNS lookups against the RFC 7208 max-of-10, identifies multiple records (which break SPF entirely), and lists authorized senders. Use to verify after adding a record, or to understand what an existing record actually does. Target MUST be on the scan allowlist.","input_schema":{"type":"object","properties":{"domain":{"type":"string","description":"Domain to check"}},"required":["domain"]}},
    {"name":"spf_generate","description":"Generate a recommended SPF TXT record for a domain Sean owns given a list of authorized senders. Pass senders as a comma-separated string or list using known short names (easywp, google, outlook, icloud, mailchimp, sendgrid, mailgun, ses, namecheap-forwarding, etc.) or raw directives (include:spf.example.com, ip4:1.2.3.0/24). Qualifier is one of: softfail (~all, RECOMMENDED START), hardfail (-all, FINAL stage only), neutral (?all, monitor only - avoid). With no senders argument, returns the list of known providers. NEVER writes DNS - generation only. Target MUST be on the scan allowlist.","input_schema":{"type":"object","properties":{"domain":{"type":"string","description":"Domain to generate SPF for"},"senders":{"type":"string","description":"Comma-separated list of provider names or raw directives, e.g. \"easywp\" or \"google,mailchimp\""},"qualifier":{"type":"string","enum":["softfail","hardfail","neutral"],"default":"softfail","description":"Start with softfail (~all). Move to hardfail (-all) only after weeks of clean DMARC reports."}},"required":["domain"]}},
    {"name":"dkim_check","description":"Check DKIM records for a domain Sean owns. If selectors not given, probes a list of common selectors (default, selector1, google, k1, easywp, fm1, etc.). Parses each found record for key size (flags <2048 bits as weak), test mode (t=y flag), and revoked keys (empty p=). DKIM selector names are chosen by the mail provider; if your provider uses a custom name not in the common list, pass it explicitly. Target MUST be on the scan allowlist.","input_schema":{"type":"object","properties":{"domain":{"type":"string","description":"Domain to check"},"selectors":{"type":"string","description":"Optional comma-separated list of selector names to check, e.g. \"selector1,selector2\". If omitted, probes common selectors."}},"required":["domain"]}},
    {"name":"tls_audit","description":"Deep TLS posture audit beyond cert_check: probes each TLS protocol version (1.0/1.1/1.2/1.3) separately to identify which the server accepts, walks the full certificate chain (not just leaf), checks for forward secrecy in negotiated cipher, flags CBC/RC4/3DES weak ciphers, validates HSTS configuration. Target MUST be on the scan allowlist. Accepts host or host:port (default 443).","input_schema":{"type":"object","properties":{"host_or_port":{"type":"string","description":"Host to audit, optionally with :port (default 443). E.g. hollowed-ground.com or hollowed-ground.com:443"}},"required":["host_or_port"]}},
]

# ---- Dispatch wrappers + map ----

def _dispatch_epss_lookup(inputs):
    _cve = inputs.get("cve_id","").strip()
    if not _cve: return "ERROR: epss_lookup requires cve_id."
    return epss_lookup(_cve)


def _dispatch_kev_check(inputs):
    _cve = inputs.get("cve_id","").strip()
    if not _cve: return "ERROR: kev_check requires cve_id."
    return kev_check(_cve)


def _dispatch_cve_lookup(inputs):
    _cve = inputs.get("cve_id","").strip()
    if not _cve: return "ERROR: cve_lookup requires cve_id."
    return cve_lookup(_cve)


def _dispatch_cve_enrich(inputs):
    _cve = inputs.get("cve_id","").strip()
    if not _cve: return "ERROR: cve_enrich requires cve_id."
    return cve_enrich(_cve)


def _dispatch_dns_audit(inputs):
    _d = inputs.get("domain","").strip()
    if not _d: return "ERROR: dns_audit requires domain."
    return dns_audit(_d)


def _dispatch_cert_check(inputs):
    _h = inputs.get("host","").strip()
    if not _h: return "ERROR: cert_check requires host."
    return cert_check(_h)


def _dispatch_subdomain_enum(inputs):
    _d = inputs.get("domain","").strip()
    if not _d: return "ERROR: subdomain_enum requires domain."
    return subdomain_enum(_d)


def _dispatch_http_headers(inputs):
    _u = inputs.get("url","").strip()
    if not _u: return "ERROR: http_headers requires url."
    return http_headers(_u)


def _dispatch_dmarc_check(inputs):
    _d = inputs.get("domain","").strip()
    if not _d: return "ERROR: dmarc_check requires domain."
    return dmarc_check(_d)


def _dispatch_dmarc_generate(inputs):
    _d = inputs.get("domain","").strip()
    _p = inputs.get("phase","monitor").strip().lower()
    _r = inputs.get("report_email","").strip() or None
    if not _d: return "ERROR: dmarc_generate requires domain."
    return dmarc_generate(_d, _p, _r)


def _dispatch_spf_check(inputs):
    _d = inputs.get("domain","").strip()
    if not _d: return "ERROR: spf_check requires domain."
    return spf_check(_d)


def _dispatch_spf_generate(inputs):
    _d = inputs.get("domain","").strip()
    _s = inputs.get("senders","")
    _q = inputs.get("qualifier","softfail").strip().lower()
    if not _d: return "ERROR: spf_generate requires domain."
    return spf_generate(_d, _s, _q)


def _dispatch_dkim_check(inputs):
    _d = inputs.get("domain","").strip()
    _s = inputs.get("selectors","") or None
    if not _d: return "ERROR: dkim_check requires domain."
    return dkim_check(_d, _s)


def _dispatch_tls_audit(inputs):
    _h = inputs.get("host_or_port","").strip()
    if not _h: return "ERROR: tls_audit requires host_or_port."
    return tls_audit(_h)


DISPATCH = {
    "epss_lookup": _dispatch_epss_lookup,
    "kev_check": _dispatch_kev_check,
    "cve_lookup": _dispatch_cve_lookup,
    "cve_enrich": _dispatch_cve_enrich,
    "dns_audit": _dispatch_dns_audit,
    "cert_check": _dispatch_cert_check,
    "subdomain_enum": _dispatch_subdomain_enum,
    "http_headers": _dispatch_http_headers,
    "dmarc_check": _dispatch_dmarc_check,
    "dmarc_generate": _dispatch_dmarc_generate,
    "spf_check": _dispatch_spf_check,
    "spf_generate": _dispatch_spf_generate,
    "dkim_check": _dispatch_dkim_check,
    "tls_audit": _dispatch_tls_audit,
}
