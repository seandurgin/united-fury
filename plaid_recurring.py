"""Plaid recurring/upcoming bills detection.

Uses /transactions/recurring/get which auto-detects subscriptions and
predicted next-charge dates per Plaid's transaction-stream analysis.

Returns a formatted summary suitable for both Telegram replies and the
morning briefing money block."""
from datetime import datetime, timedelta
import logging

log = logging.getLogger("clawdia.plaid_recurring")


def get_recurring_streams(days_ahead=14):
    """Return list of recurring transaction streams across all linked
    accounts. days_ahead caps the projection window for upcoming bills.
    Returns dict: {
        'subscriptions': [...],   # active outflow streams
        'income': [...],          # active inflow streams
        'upcoming': [...],        # streams with predicted_next_date in window
        'errors': [...]
    }"""
    from plaid_finance import load_tokens, get_plaid_client
    from plaid.model.transactions_recurring_get_request import TransactionsRecurringGetRequest

    tokens = load_tokens()
    if not tokens:
        return {"subscriptions": [], "income": [], "upcoming": [], "errors": ["No banks connected"]}

    client = get_plaid_client()
    subscriptions = []
    income = []
    errors = []
    today = datetime.now().date()
    cutoff = today + timedelta(days=days_ahead)

    for institution, data in tokens.items():
        try:
            # transactions_recurring_get requires explicit account_ids; fetch them first
            from plaid.model.accounts_get_request import AccountsGetRequest
            acct_resp = client.accounts_get(AccountsGetRequest(access_token=data["access_token"]))
            account_ids = [a.account_id for a in acct_resp.accounts]
            if not account_ids:
                continue

            req = TransactionsRecurringGetRequest(
                access_token=data["access_token"],
                account_ids=account_ids
            )
            resp = client.transactions_recurring_get(req)

            for stream in (resp.outflow_streams or []):
                if str(stream.status) != "RecurringTransactionFrequency.MATURE" and \
                   str(stream.status).split(".")[-1] not in ("MATURE", "EARLY_DETECTION"):
                    # Skip uncertain streams
                    pass
                avg_amount = float(stream.average_amount.amount or 0)
                last_amount = float(stream.last_amount.amount or 0) if stream.last_amount else avg_amount
                merchant = stream.merchant_name or stream.description or "Unknown"
                freq = str(stream.frequency).split(".")[-1].lower()
                last_date = stream.last_date
                next_date = stream.predicted_next_date
                is_active = bool(stream.is_active)
                status = str(stream.status).split(".")[-1]
                subscriptions.append({
                    "institution": institution,
                    "merchant": merchant,
                    "amount": abs(avg_amount),
                    "last_amount": abs(last_amount),
                    "frequency": freq,
                    "last_date": last_date.isoformat() if last_date else None,
                    "next_date": next_date.isoformat() if next_date else None,
                    "is_active": is_active,
                    "status": status,
                    "category": (stream.personal_finance_category.primary
                                 if stream.personal_finance_category else None),
                })

            for stream in (resp.inflow_streams or []):
                avg_amount = float(stream.average_amount.amount or 0)
                merchant = stream.merchant_name or stream.description or "Unknown"
                freq = str(stream.frequency).split(".")[-1].lower()
                income.append({
                    "institution": institution,
                    "source": merchant,
                    "amount": abs(avg_amount),
                    "frequency": freq,
                    "last_date": stream.last_date.isoformat() if stream.last_date else None,
                    "next_date": stream.predicted_next_date.isoformat() if stream.predicted_next_date else None,
                    "is_active": bool(stream.is_active),
                })
        except Exception as e:
            errors.append(f"{institution}: {str(e)[:120]}")
            log.warning("recurring fetch failed for %s: %s", institution, e)

    # Compute upcoming subset
    upcoming = []
    for sub in subscriptions:
        if not sub.get("next_date"):
            continue
        try:
            nd = datetime.fromisoformat(sub["next_date"]).date()
            if today <= nd <= cutoff:
                sub_with_days = dict(sub)
                sub_with_days["days_away"] = (nd - today).days
                upcoming.append(sub_with_days)
        except Exception:
            pass
    upcoming.sort(key=lambda x: x.get("days_away", 999))

    return {
        "subscriptions": subscriptions,
        "income": income,
        "upcoming": upcoming,
        "errors": errors,
    }


def format_recurring_summary(active_only=True, max_subs=20):
    """Format a human-readable summary of recurring charges. Used by the
    plaid_recurring tool and (in compact form) by the morning briefing."""
    data = get_recurring_streams(days_ahead=14)
    if data["errors"] and not data["subscriptions"] and not data["income"]:
        return "Could not fetch recurring data:\n  " + "\n  ".join(data["errors"])

    lines = []
    subs = data["subscriptions"]
    if active_only:
        subs = [s for s in subs if s["is_active"]]
    subs.sort(key=lambda s: s["amount"], reverse=True)

    if subs:
        monthly_equiv = 0
        for s in subs:
            f = s["frequency"]
            a = s["amount"]
            if f in ("monthly", "semi_monthly"):
                monthly_equiv += a
            elif f == "weekly":
                monthly_equiv += a * 4.33
            elif f == "biweekly":
                monthly_equiv += a * 2.17
            elif f == "annually":
                monthly_equiv += a / 12

        lines.append(f"Recurring outflows ({len(subs)} active streams, ~${monthly_equiv:,.2f}/mo):")
        for s in subs[:max_subs]:
            f = s["frequency"]
            tag = f"[{f}]" if f else ""
            cat = f" ({s['category']})" if s.get("category") else ""
            next_s = f" next ~{s['next_date']}" if s.get("next_date") else ""
            lines.append(f"  • {s['merchant']}: ${s['amount']:.2f} {tag}{cat}{next_s}")
        if len(subs) > max_subs:
            lines.append(f"  ... and {len(subs) - max_subs} more")
    else:
        lines.append("No recurring outflows detected.")

    if data["income"]:
        lines.append("")
        lines.append(f"Recurring income ({len(data['income'])} streams):")
        for inc in data["income"][:5]:
            f = inc["frequency"]
            lines.append(f"  • {inc['source']}: ${inc['amount']:.2f} [{f}]")

    if data["upcoming"]:
        lines.append("")
        lines.append(f"Upcoming bills (next 14 days, {len(data['upcoming'])} items):")
        for u in data["upcoming"]:
            d = u["days_away"]
            day_label = "today" if d == 0 else ("tomorrow" if d == 1 else f"in {d}d")
            lines.append(f"  • {u['merchant']}: ${u['amount']:.2f} {day_label} ({u['next_date']})")

    if data["errors"]:
        lines.append("")
        lines.append("Partial errors:")
        for e in data["errors"]:
            lines.append(f"  ! {e}")

    return "\n".join(lines)


def upcoming_bills_summary(days_ahead=7):
    """Compact briefing-ready summary: just the bills hitting in N days."""
    data = get_recurring_streams(days_ahead=days_ahead)
    if not data["upcoming"]:
        return None
    total = sum(u["amount"] for u in data["upcoming"])
    lines = [f"{len(data['upcoming'])} bills in next {days_ahead}d, total ~${total:,.2f}:"]
    for u in data["upcoming"][:8]:
        d = u["days_away"]
        day_label = "today" if d == 0 else ("tom" if d == 1 else f"in {d}d")
        lines.append(f"  • {u['merchant']}: ${u['amount']:.2f} {day_label}")
    return "\n".join(lines)
