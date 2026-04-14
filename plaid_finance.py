"""Plaid financial integration for Clawdia."""
import os, json, logging
from datetime import datetime, timedelta
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
from plaid import Configuration, ApiClient, Environment

log = logging.getLogger("clawdia.plaid")

PLAID_CLIENT_ID = os.environ.get("PLAID_CLIENT_ID", "")
PLAID_SECRET    = os.environ.get("PLAID_SECRET", "")
PLAID_ENV       = os.environ.get("PLAID_ENV", "sandbox")
TOKEN_FILE      = "/etc/clawdia/plaid_tokens.json"

def get_plaid_client():
    env = Environment.Sandbox if PLAID_ENV == "sandbox" else Environment.Production
    config = Configuration(host=env, api_key={"clientId": PLAID_CLIENT_ID, "secret": PLAID_SECRET})
    return plaid_api.PlaidApi(ApiClient(config))

def load_tokens():
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_tokens(tokens):
    with open(TOKEN_FILE, 'w') as f:
        json.dump(tokens, f)
    os.chmod(TOKEN_FILE, 0o600)

def exchange_public_token(public_token, institution_name):
    """Exchange a public token from Link for an access token."""
    try:
        client = get_plaid_client()
        request = ItemPublicTokenExchangeRequest(public_token=public_token)
        response = client.item_public_token_exchange(request)
        tokens = load_tokens()
        tokens[institution_name] = {
            "access_token": response.access_token,
            "item_id": response.item_id,
            "added": datetime.now().isoformat()
        }
        save_tokens(tokens)
        return f"Successfully connected {institution_name}."
    except Exception as e:
        return f"Error connecting account: {e}"

def get_accounts():
    """Get all account balances."""
    tokens = load_tokens()
    if not tokens:
        return "No bank accounts connected. Use /plaidlink to connect an account."
    client = get_plaid_client()
    output = []
    total_assets = 0
    total_debt = 0
    for institution, data in tokens.items():
        try:
            request = AccountsGetRequest(access_token=data["access_token"])
            response = client.accounts_get(request)
            output.append(f"\n{institution}:")
            for acct in response.accounts:
                balance = acct.balances.current or 0
                acct_type = acct.type.value
                subtype = acct.subtype.value if acct.subtype else ""
                name = acct.name
                mask = acct.mask or "????"
                if acct_type in ("credit", "loan"):
                    output.append(f"  {name} (...{mask}): ${balance:,.2f} owed [{subtype}]")
                    total_debt += balance
                else:
                    output.append(f"  {name} (...{mask}): ${balance:,.2f} [{subtype}]")
                    total_assets += balance
        except Exception as e:
            output.append(f"  Error fetching {institution}: {e}")
    output.append(f"\nTotal assets: ${total_assets:,.2f}")
    output.append(f"Total debt: ${total_debt:,.2f}")
    output.append(f"Net: ${total_assets - total_debt:,.2f}")
    return "\n".join(output)

def get_transactions(days=30, max_results=50):
    """Get recent transactions across all accounts."""
    tokens = load_tokens()
    if not tokens:
        return "No bank accounts connected."
    client = get_plaid_client()
    end_date = datetime.now().date()
    start_date = (datetime.now() - timedelta(days=days)).date()
    all_transactions = []
    for institution, data in tokens.items():
        try:
            request = TransactionsGetRequest(
                access_token=data["access_token"],
                start_date=start_date,
                end_date=end_date,
                options=TransactionsGetRequestOptions(count=max_results)
            )
            response = client.transactions_get(request)
            for txn in response.transactions:
                all_transactions.append({
                    "date": str(txn.date),
                    "name": txn.name,
                    "amount": txn.amount,
                    "category": txn.category[0] if txn.category else "Other",
                    "institution": institution
                })
        except Exception as e:
            all_transactions.append({"error": f"{institution}: {e}"})
    if not all_transactions:
        return f"No transactions found in the last {days} days."
    all_transactions.sort(key=lambda x: x.get("date", ""), reverse=True)
    lines = [f"Transactions (last {days} days):"]
    total_spent = 0
    for t in all_transactions[:max_results]:
        if "error" in t:
            lines.append(f"  Error: {t['error']}")
            continue
        amount = t["amount"]
        if amount > 0:
            total_spent += amount
            lines.append(f"  {t['date']} | {t['name'][:30]} | ${amount:,.2f} | {t['category']}")
        else:
            lines.append(f"  {t['date']} | {t['name'][:30]} | +${abs(amount):,.2f} (credit)")
    lines.append(f"\nTotal spent: ${total_spent:,.2f}")
    return "\n".join(lines)

def get_debt_snapshot():
    """Get current debt balances and save snapshot to memory."""
    tokens = load_tokens()
    if not tokens:
        return "No bank accounts connected.", {}
    client = get_plaid_client()
    debts = {}
    for institution, data in tokens.items():
        try:
            request = AccountsGetRequest(access_token=data["access_token"])
            response = client.accounts_get(request)
            for acct in response.accounts:
                if acct.type.value in ("credit", "loan"):
                    balance = acct.balances.current or 0
                    key = f"{institution}_{acct.name}"
                    debts[key] = balance
        except Exception as e:
            log.error("Debt snapshot error for %s: %s", institution, e)
    return debts

def spending_by_category(days=30):
    """Summarize spending by category."""
    tokens = load_tokens()
    if not tokens:
        return "No bank accounts connected."
    client = get_plaid_client()
    end_date = datetime.now().date()
    start_date = (datetime.now() - timedelta(days=days)).date()
    categories = {}
    for institution, data in tokens.items():
        try:
            request = TransactionsGetRequest(
                access_token=data["access_token"],
                start_date=start_date,
                end_date=end_date,
                options=TransactionsGetRequestOptions(count=500)
            )
            response = client.transactions_get(request)
            for txn in response.transactions:
                if txn.amount > 0:
                    cat = txn.category[0] if txn.category else "Other"
                    categories[cat] = categories.get(cat, 0) + txn.amount
        except Exception as e:
            log.error("Category spending error: %s", e)
    if not categories:
        return f"No spending data found for the last {days} days."
    sorted_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)
    lines = [f"Spending by category (last {days} days):"]
    total = sum(v for v in categories.values())
    for cat, amount in sorted_cats:
        pct = (amount / total) * 100
        lines.append(f"  {cat}: ${amount:,.2f} ({pct:.1f}%)")
    lines.append(f"\nTotal: ${total:,.2f}")
    return "\n".join(lines)
