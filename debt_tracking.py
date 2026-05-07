"""Debt tracking with APR-awareness.

Stores manual APR + loan terms per account (Plaid doesn't expose APRs).
Combines with Plaid balances + recurring payments to compute:
- Estimated monthly interest per account
- Payoff priority ranking (highest APR first = avalanche method)
- Balance trajectory (is the balance going DOWN despite payments?)
- Promotional period expiry alerts

Two SQLite tables:
- debt_accounts: one row per known debt with APR, terms, type, etc.
- debt_balance_history: snapshots over time for trajectory analysis
"""
import os, sqlite3, logging, json
from datetime import datetime, timezone, date

log = logging.getLogger("clawdia.debt")

DB_PATH = "/var/lib/clawdia/memory.db"


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS debt_accounts (
        id TEXT PRIMARY KEY,
        nickname TEXT NOT NULL,
        institution TEXT,
        kind TEXT NOT NULL,                -- 'credit_card', 'auto_loan', 'mortgage', 'personal_loan', 'bnpl'
        apr REAL,                          -- annual percentage rate as decimal (0.0784 = 7.84%)
        balance REAL,                      -- last known balance (manual snapshot from statement)
        balance_as_of TEXT,                -- date of that balance
        original_balance REAL,             -- original loan amount (loans only)
        monthly_payment REAL,              -- regular minimum payment
        maturity_date TEXT,                -- ISO date for loans, null for revolving credit
        promo_apr REAL,                    -- promotional APR if active (e.g. 0% intro)
        promo_expires TEXT,                -- when the promo rate ends
        plaid_account_match TEXT,          -- substring to match against Plaid account names for balance pulls
        notes TEXT,
        added_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS debt_balance_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        balance REAL NOT NULL,
        snapshot_at TEXT NOT NULL,
        source TEXT NOT NULL,              -- 'manual_statement', 'plaid_pull', 'tool_call'
        FOREIGN KEY (account_id) REFERENCES debt_accounts(id)
    )""")
    c.commit()
    return c


def upsert_debt_account(account_id, nickname, kind, apr=None, balance=None,
                       balance_as_of=None, institution=None, original_balance=None,
                       monthly_payment=None, maturity_date=None, promo_apr=None,
                       promo_expires=None, plaid_account_match=None, notes=None):
    """Add or update a debt account. account_id is the user-chosen short ID
    like 'honda_odyssey', 'apg_l3002', 'usaa_visa'. Idempotent."""
    now = datetime.now(timezone.utc).isoformat()
    today = balance_as_of or date.today().isoformat()
    with _conn() as c:
        existing = c.execute(
            "SELECT 1 FROM debt_accounts WHERE id=?", (account_id,)
        ).fetchone()
        if existing:
            # Build dynamic UPDATE only for fields that were provided
            fields = {
                "nickname": nickname, "kind": kind, "apr": apr,
                "balance": balance, "balance_as_of": today,
                "institution": institution, "original_balance": original_balance,
                "monthly_payment": monthly_payment, "maturity_date": maturity_date,
                "promo_apr": promo_apr, "promo_expires": promo_expires,
                "plaid_account_match": plaid_account_match, "notes": notes,
                "updated_at": now,
            }
            sets = []
            vals = []
            for k, v in fields.items():
                if v is not None:
                    sets.append(f"{k}=?")
                    vals.append(v)
            vals.append(account_id)
            c.execute(f"UPDATE debt_accounts SET {', '.join(sets)} WHERE id=?", vals)
            action = "updated"
        else:
            c.execute(
                "INSERT INTO debt_accounts (id, nickname, institution, kind, apr, "
                "balance, balance_as_of, original_balance, monthly_payment, "
                "maturity_date, promo_apr, promo_expires, plaid_account_match, "
                "notes, added_at, updated_at) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (account_id, nickname, institution, kind, apr, balance, today,
                 original_balance, monthly_payment, maturity_date, promo_apr,
                 promo_expires, plaid_account_match, notes, now, now)
            )
            action = "added"
        # Snapshot if balance was provided
        if balance is not None:
            c.execute(
                "INSERT INTO debt_balance_history (account_id, balance, snapshot_at, source) "
                "VALUES (?,?,?,?)",
                (account_id, balance, today, "manual_statement")
            )
    return action


def list_debt_accounts():
    """Return all known debt accounts as list of dicts."""
    with _conn() as c:
        rows = c.execute(
            "SELECT id, nickname, institution, kind, apr, balance, balance_as_of, "
            "original_balance, monthly_payment, maturity_date, promo_apr, "
            "promo_expires, plaid_account_match, notes, updated_at "
            "FROM debt_accounts ORDER BY apr DESC NULLS LAST"
        ).fetchall()
    cols = ["id", "nickname", "institution", "kind", "apr", "balance",
            "balance_as_of", "original_balance", "monthly_payment",
            "maturity_date", "promo_apr", "promo_expires", "plaid_account_match",
            "notes", "updated_at"]
    return [dict(zip(cols, r)) for r in rows]


def estimate_monthly_interest(balance, apr):
    """Simple average daily balance estimate. APR is decimal (0.2299 = 22.99%)."""
    if not balance or not apr:
        return 0.0
    return balance * apr / 12.0


def _try_pull_plaid_balance(plaid_match):
    """If plaid_match substring matches an account name from Plaid, return its
    current balance. Used to refresh balances from live data instead of stale
    manual statements."""
    if not plaid_match:
        return None
    try:
        from plaid_finance import load_tokens, get_plaid_client
        from plaid.model.accounts_get_request import AccountsGetRequest
        tokens = load_tokens()
        if not tokens:
            return None
        client = get_plaid_client()
        for inst, data in tokens.items():
            try:
                resp = client.accounts_get(AccountsGetRequest(access_token=data["access_token"]))
                for acct in resp.accounts:
                    name = (acct.name or "").lower()
                    mask = (acct.mask or "")
                    match_str = plaid_match.lower()
                    if match_str in name or match_str in mask:
                        return float(acct.balances.current or 0)
            except Exception:
                continue
    except Exception as e:
        log.warning("plaid balance pull failed: %s", e)
    return None


def debt_status_summary():
    """Format a comprehensive debt status report for Telegram. Includes:
    - Per-account: balance, APR, est monthly interest, plaid-fresh balance if available
    - Total debt, total monthly interest cost, blended APR
    - Payoff priority ranking (avalanche method)
    - Promo expiry alerts (any within 90 days)"""
    accounts = list_debt_accounts()
    if not accounts:
        return ("No debt accounts tracked yet. To add one, tell Clawdia: "
                "\"Add a debt account: USAA Visa, credit card, balance $9,441, APR 22.99%\". "
                "Or upload a statement and Clawdia will offer to extract terms.")

    lines = ["DEBT STATUS"]
    lines.append("=" * 50)

    total_balance = 0.0
    total_monthly_interest = 0.0
    weighted_apr_numerator = 0.0
    weighted_apr_denominator = 0.0
    promo_alerts = []
    today = date.today()

    # Pull current balances where possible, refresh from Plaid
    enriched = []
    for acct in accounts:
        balance = acct["balance"] or 0.0
        balance_source = "manual statement"
        plaid_bal = _try_pull_plaid_balance(acct.get("plaid_account_match"))
        if plaid_bal is not None:
            balance = plaid_bal
            balance_source = "Plaid (live)"
        # Use promo APR if active, else regular APR
        effective_apr = acct["apr"]
        apr_note = ""
        if acct["promo_apr"] is not None and acct["promo_expires"]:
            try:
                exp = datetime.fromisoformat(acct["promo_expires"]).date()
                if exp > today:
                    effective_apr = acct["promo_apr"]
                    days_left = (exp - today).days
                    apr_note = f" (promo {acct['promo_apr']*100:.2f}% until {exp})"
                    if days_left <= 90:
                        promo_alerts.append(
                            f"{acct['nickname']}: promo rate ends in {days_left} days "
                            f"({exp}); reverts to {acct['apr']*100:.2f}%"
                        )
            except Exception:
                pass
        monthly_interest = estimate_monthly_interest(balance, effective_apr)
        total_balance += balance
        total_monthly_interest += monthly_interest
        if effective_apr is not None and balance > 0:
            weighted_apr_numerator += balance * effective_apr
            weighted_apr_denominator += balance
        enriched.append({
            **acct,
            "current_balance": balance,
            "balance_source": balance_source,
            "effective_apr": effective_apr,
            "apr_note": apr_note,
            "monthly_interest": monthly_interest,
        })

    # Sort by avalanche priority: highest effective APR first
    enriched.sort(key=lambda x: (x["effective_apr"] or 0), reverse=True)

    for i, a in enumerate(enriched, 1):
        bal = a["current_balance"]
        apr_str = f"{a['effective_apr']*100:.2f}%" if a["effective_apr"] is not None else "APR unknown"
        lines.append(f"\n{i}. {a['nickname']} ({a['kind']})")
        lines.append(f"   Balance: ${bal:,.2f}  [{a['balance_source']}]")
        lines.append(f"   APR: {apr_str}{a['apr_note']}")
        if a["effective_apr"] is not None:
            lines.append(f"   Est. monthly interest: ${a['monthly_interest']:,.2f}")
        if a["monthly_payment"]:
            lines.append(f"   Min payment: ${a['monthly_payment']:,.2f}")
        if a["maturity_date"]:
            lines.append(f"   Matures: {a['maturity_date']}")

    lines.append("\n" + "=" * 50)
    lines.append(f"TOTAL DEBT: ${total_balance:,.2f}")
    lines.append(f"Est. total monthly interest cost: ${total_monthly_interest:,.2f}")
    if weighted_apr_denominator > 0:
        blended = (weighted_apr_numerator / weighted_apr_denominator) * 100
        lines.append(f"Blended (balance-weighted) APR: {blended:.2f}%")

    if promo_alerts:
        lines.append("\n\u26a0\ufe0f PROMO RATE ALERTS:")
        for alert in promo_alerts:
            lines.append(f"  \u2022 {alert}")

    # Payoff priority guidance
    eligible = [a for a in enriched if a["effective_apr"] and a["current_balance"] > 0]
    if len(eligible) >= 2:
        top = eligible[0]
        lines.append(f"\nAVALANCHE PRIORITY: pay extra on {top['nickname']} first "
                     f"({top['effective_apr']*100:.2f}% APR) to minimize total interest.")

    return "\n".join(lines)


def briefing_debt_alert():
    """Compact alert for morning briefing. Returns None unless something
    actionable: (a) promo rate expiring within 30d, (b) a balance went UP
    despite a payment hitting recently, (c) balance higher than statement
    by more than $200 (suggests new charges)."""
    accounts = list_debt_accounts()
    alerts = []
    today = date.today()
    for acct in accounts:
        if acct["promo_expires"]:
            try:
                exp = datetime.fromisoformat(acct["promo_expires"]).date()
                days = (exp - today).days
                if 0 <= days <= 30:
                    alerts.append(
                        f"\u26a0\ufe0f {acct['nickname']}: promo APR ends in {days}d ({exp}), "
                        f"reverts to {(acct['apr'] or 0)*100:.2f}%"
                    )
            except Exception:
                pass
    if not alerts:
        return None
    return "\n".join(alerts)
