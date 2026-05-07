"""Net worth calculation: liquid (Plaid) + RSU (Yahoo Finance ORCL) + manual
illiquid assets (home, vehicles).

Manual assets and ORCL grant info live in a new SQLite table 'net_worth_assets'
so they can be updated via Telegram conversation without redeploying code.

Weekly snapshots go to 'net_worth_snapshots' for trajectory tracking."""
import os, sqlite3, logging, requests
from datetime import datetime, timezone, date

log = logging.getLogger("clawdia.net_worth")

DB_PATH = "/var/lib/clawdia/memory.db"


# ─────────────── DB schema + seeding ───────────────

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS net_worth_assets (
        name TEXT PRIMARY KEY,
        kind TEXT NOT NULL,          -- 'home', 'vehicle', 'rsu_grant'
        value REAL NOT NULL,
        meta TEXT,                   -- JSON for kind-specific extras (shares, grant_date, etc.)
        updated_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS net_worth_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_at TEXT NOT NULL,
        liquid REAL NOT NULL,
        debt REAL NOT NULL,
        rsu_value REAL NOT NULL,
        rsu_vested_value REAL NOT NULL,
        manual_assets REAL NOT NULL,
        net_worth REAL NOT NULL,
        details TEXT
    )""")
    c.commit()
    return c


def seed_initial_assets():
    """Seed the manual assets if the table is empty. Called on first net_worth
    invocation. Values come from Sean's input on 2026-04-30."""
    import json
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        existing = c.execute("SELECT COUNT(*) FROM net_worth_assets").fetchone()[0]
        if existing > 0:
            return False  # already seeded
        defaults = [
            ("home_north_east_md", "home", 550000.0,
             json.dumps({"address": "North East, MD", "source": "Sean estimate 2026-04-30"}), now),
            ("ford_f350", "vehicle", 70000.0,
             json.dumps({"make": "Ford", "model": "F-350", "source": "Sean estimate 2026-04-30"}), now),
            ("family_van", "vehicle", 40000.0,
             json.dumps({"source": "Sean estimate 2026-04-30"}), now),
            ("oracle_rsu_grant_2026", "rsu_grant", 0.0,  # value computed live from price * vested shares
             json.dumps({
                 "ticker": "ORCL",
                 "total_shares": 416,
                 "grant_date": "2026-01-05",
                 "vest_schedule": "quarterly_4yr",  # 25% per year, vested quarterly
                 "first_vest_offset_months": 12,    # standard 1-year cliff
             }), now),
        ]
        c.executemany(
            "INSERT INTO net_worth_assets (name, kind, value, meta, updated_at) VALUES (?,?,?,?,?)",
            defaults
        )
    log.info("net_worth: seeded 4 default assets")
    return True


def update_manual_asset(name, value):
    """Update the value of a manual asset (home, vehicle). Used when Sean
    refines an estimate via Telegram."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            "UPDATE net_worth_assets SET value=?, updated_at=? WHERE name=?",
            (float(value), now, name)
        )
        return cur.rowcount


# ─────────────── Yahoo Finance ORCL price ───────────────

def fetch_orcl_price():
    """Pull current ORCL price from Yahoo Finance v8 chart endpoint.
    Free, no key, stable for years. Returns float USD or None on failure."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/ORCL",
            params={"interval": "1d", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0 (compatible; ClawdiaNetWorth/1.0)"},
            timeout=10
        )
        if r.status_code != 200:
            log.warning("Yahoo finance returned %d", r.status_code)
            return None
        data = r.json()
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        meta = result[0].get("meta", {})
        # regularMarketPrice is the current price
        price = meta.get("regularMarketPrice")
        if price:
            return float(price)
        return None
    except Exception as e:
        log.warning("ORCL price fetch failed: %s", e)
        return None


# ─────────────── RSU vesting math ───────────────

def compute_rsu_status(grant_meta, current_price):
    """Compute vested/unvested share counts and dollar values.

    Standard Oracle grant: 4-year quarterly vest with 1-year cliff.
    25% (104 of 416) vests at month 12, then 6.25% (26 shares) every 3 months."""
    import json
    if isinstance(grant_meta, str):
        grant_meta = json.loads(grant_meta)
    total = int(grant_meta.get("total_shares", 0))
    if total == 0 or not current_price:
        return {"total_shares": total, "vested_shares": 0, "unvested_shares": total,
                "vested_value": 0, "unvested_value": 0, "total_value": 0,
                "next_vest_date": None, "next_vest_shares": 0}

    grant_date_str = grant_meta.get("grant_date", "2026-01-05")
    grant_date = datetime.strptime(grant_date_str, "%Y-%m-%d").date()
    today = date.today()

    cliff_months = int(grant_meta.get("first_vest_offset_months", 12))
    # Days since grant
    months_since = (today.year - grant_date.year) * 12 + (today.month - grant_date.month)
    if today.day < grant_date.day:
        months_since -= 1

    if months_since < cliff_months:
        vested = 0
        # Next vest is the cliff date
        next_vest_year = grant_date.year + (cliff_months // 12)
        next_vest_month = grant_date.month + (cliff_months % 12)
        if next_vest_month > 12:
            next_vest_month -= 12
            next_vest_year += 1
        try:
            next_vest_date = date(next_vest_year, next_vest_month, grant_date.day)
        except ValueError:
            next_vest_date = date(next_vest_year, next_vest_month, 28)
        next_vest_shares = total // 4  # 25% at cliff
    else:
        # 25% at cliff, then 6.25% every 3 months thereafter
        cliff_shares = total // 4
        quarters_after_cliff = (months_since - cliff_months) // 3
        post_cliff_per_quarter = total // 16  # 6.25% per quarter for 12 quarters
        vested = cliff_shares + quarters_after_cliff * post_cliff_per_quarter
        if vested > total:
            vested = total
        # Next vest date
        if vested >= total:
            next_vest_date = None
            next_vest_shares = 0
        else:
            quarters_passed = quarters_after_cliff + 1
            next_offset_months = cliff_months + quarters_passed * 3
            next_y = grant_date.year + (next_offset_months // 12)
            next_m = grant_date.month + (next_offset_months % 12)
            if next_m > 12:
                next_m -= 12
                next_y += 1
            try:
                next_vest_date = date(next_y, next_m, grant_date.day)
            except ValueError:
                next_vest_date = date(next_y, next_m, 28)
            next_vest_shares = post_cliff_per_quarter

    unvested = total - vested
    return {
        "total_shares": total,
        "vested_shares": vested,
        "unvested_shares": unvested,
        "vested_value": vested * current_price,
        "unvested_value": unvested * current_price,
        "total_value": total * current_price,
        "next_vest_date": next_vest_date.isoformat() if next_vest_date else None,
        "next_vest_shares": next_vest_shares,
        "current_price": current_price,
    }


# ─────────────── Main computation ───────────────

def compute_net_worth():
    """Compute current net worth. Returns dict with breakdown."""
    import json
    seed_initial_assets()  # idempotent

    # 1. Plaid liquid + debt
    from plaid_finance import load_tokens, get_plaid_client
    from plaid.model.accounts_get_request import AccountsGetRequest
    tokens = load_tokens()
    liquid = 0.0
    debt = 0.0
    plaid_breakdown = []
    plaid_errors = []
    if tokens:
        client = get_plaid_client()
        for institution, data in tokens.items():
            try:
                resp = client.accounts_get(AccountsGetRequest(access_token=data["access_token"]))
                inst_total = 0.0
                for acct in resp.accounts:
                    bal = float(acct.balances.current or 0)
                    acct_type = acct.type.value
                    if acct_type in ("credit", "loan"):
                        debt += bal
                        inst_total -= bal
                    else:
                        liquid += bal
                        inst_total += bal
                plaid_breakdown.append((institution, inst_total))
            except Exception as e:
                plaid_errors.append(f"{institution}: {str(e)[:80]}")

    # 2. RSU value
    orcl_price = fetch_orcl_price()
    rsu_status = None
    with _conn() as c:
        row = c.execute(
            "SELECT meta FROM net_worth_assets WHERE kind='rsu_grant' LIMIT 1"
        ).fetchone()
    if row and orcl_price:
        rsu_status = compute_rsu_status(row[0], orcl_price)

    rsu_total_value = rsu_status["total_value"] if rsu_status else 0
    rsu_vested_value = rsu_status["vested_value"] if rsu_status else 0

    # 3. Manual assets
    manual_assets = 0.0
    manual_breakdown = []
    with _conn() as c:
        rows = c.execute(
            "SELECT name, kind, value, meta, updated_at FROM net_worth_assets "
            "WHERE kind IN ('home', 'vehicle', 'college_savings', 'investment') ORDER BY value DESC"
        ).fetchall()
    for name, kind, value, meta_json, updated_at in rows:
        manual_assets += value
        try:
            meta = json.loads(meta_json) if meta_json else {}
        except Exception:
            meta = {}
        manual_breakdown.append({
            "name": name, "kind": kind, "value": value,
            "meta": meta, "updated_at": updated_at
        })

    # 4. Net worth
    # Conservative: only count VESTED RSU since unvested shares aren't yours yet
    net = liquid - debt + rsu_vested_value + manual_assets

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "liquid": liquid,
        "debt": debt,
        "plaid_breakdown": plaid_breakdown,
        "plaid_errors": plaid_errors,
        "rsu_status": rsu_status,
        "manual_assets": manual_assets,
        "manual_breakdown": manual_breakdown,
        "net_worth": net,
        "net_worth_with_unvested": net + (rsu_total_value - rsu_vested_value),
    }


def format_net_worth_summary():
    """Human-readable summary for Telegram. Snapshots to DB if it's been
    7+ days since last snapshot (idempotent on same-day calls)."""
    import json
    data = compute_net_worth()

    # Maybe snapshot
    with _conn() as c:
        last = c.execute(
            "SELECT snapshot_at FROM net_worth_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
    should_snapshot = True
    if last:
        last_dt = datetime.fromisoformat(last[0])
        days_since = (datetime.now(timezone.utc) - last_dt).days
        if days_since < 7:
            should_snapshot = False
    if should_snapshot:
        rsu = data["rsu_status"] or {}
        with _conn() as c:
            c.execute(
                "INSERT INTO net_worth_snapshots (snapshot_at, liquid, debt, rsu_value, "
                "rsu_vested_value, manual_assets, net_worth, details) VALUES (?,?,?,?,?,?,?,?)",
                (data["as_of"], data["liquid"], data["debt"],
                 rsu.get("total_value", 0), rsu.get("vested_value", 0),
                 data["manual_assets"], data["net_worth"],
                 json.dumps({"plaid_breakdown": data["plaid_breakdown"]}))
            )

    # Trajectory
    trend_line = ""
    with _conn() as c:
        prev = c.execute(
            "SELECT snapshot_at, net_worth FROM net_worth_snapshots "
            "WHERE id < (SELECT MAX(id) FROM net_worth_snapshots) "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if prev:
        delta = data["net_worth"] - prev[1]
        sign = "+" if delta >= 0 else ""
        trend_line = f"  Change since {prev[0][:10]}: {sign}${delta:,.2f}"

    lines = [f"Net worth: ${data['net_worth']:,.2f}"]
    if trend_line:
        lines.append(trend_line)
    lines.append("")
    lines.append("Liquid:")
    lines.append(f"  Total assets: ${data['liquid']:,.2f}")
    if data['debt'] > 0:
        lines.append(f"  Total debt: ${data['debt']:,.2f}")
    for inst, total in data["plaid_breakdown"]:
        lines.append(f"    {inst}: ${total:,.2f}")
    if data["plaid_errors"]:
        for e in data["plaid_errors"]:
            lines.append(f"    ! {e}")

    if data["rsu_status"]:
        r = data["rsu_status"]
        lines.append("")
        lines.append(f"Oracle RSUs (ORCL @ ${r['current_price']:.2f}):")
        lines.append(f"  Vested:   {r['vested_shares']:>4} shares  ${r['vested_value']:>12,.2f}")
        lines.append(f"  Unvested: {r['unvested_shares']:>4} shares  ${r['unvested_value']:>12,.2f}")
        lines.append(f"  Total:    {r['total_shares']:>4} shares  ${r['total_value']:>12,.2f}")
        if r.get("next_vest_date"):
            lines.append(f"  Next vest: {r['next_vest_date']} ({r['next_vest_shares']} shares)")

    if data["manual_breakdown"]:
        lines.append("")
        lines.append(f"Manual assets: ${data['manual_assets']:,.2f}")
        for m in data["manual_breakdown"]:
            lines.append(f"  {m['name']}: ${m['value']:,.2f}")

    lines.append("")
    lines.append(f"(With unvested RSU: ${data['net_worth_with_unvested']:,.2f})")
    return "\n".join(lines)


def briefing_money_block():
    """Compact 5-line money block for the morning briefing."""
    import json
    try:
        data = compute_net_worth()
    except Exception as e:
        return f"  Money: error fetching ({str(e)[:80]})"
    lines = [f"Liquid: ${data['liquid']:,.0f}  Debt: ${data['debt']:,.0f}  Net worth: ${data['net_worth']:,.0f}"]
    # Day-over-day liquid delta if we have a recent snapshot
    with _conn() as c:
        rows = c.execute(
            "SELECT snapshot_at, liquid, net_worth FROM net_worth_snapshots "
            "ORDER BY id DESC LIMIT 2"
        ).fetchall()
    if len(rows) >= 2:
        delta_liquid = data["liquid"] - rows[1][1]
        delta_net = data["net_worth"] - rows[1][2]
        sign_l = "+" if delta_liquid >= 0 else ""
        sign_n = "+" if delta_net >= 0 else ""
        lines.append(f"Since {rows[1][0][:10]}: liquid {sign_l}${delta_liquid:,.0f}, net worth {sign_n}${delta_net:,.0f}")
    if data["rsu_status"]:
        r = data["rsu_status"]
        lines.append(f"ORCL ${r['current_price']:.2f} \u2192 vested ${r['vested_value']:,.0f}, unvested ${r['unvested_value']:,.0f}")
    return "\n".join(lines)
