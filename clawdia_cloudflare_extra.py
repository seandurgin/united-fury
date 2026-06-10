"""
clawdia_cloudflare_extra.py
---------------------------
Additional Cloudflare tools for Clawdia, extending her existing
`cloudflare_dns` / `cloudflare_purge` set.

Adds:
  - cloudflare_redirect : create/list single (dynamic) redirect rules
  - cloudflare_pages    : list projects, add custom domain, trigger deploy

Auth: reuses the same Cloudflare API token pattern as her existing tools.
  Env vars (in /etc/clawdia/env):
    CLOUDFLARE_API_TOKEN     (existing — must now also have
                              "Cloudflare Pages: Edit" and
                              "Dynamic Redirect: Edit" scopes)
    CLOUDFLARE_ACCOUNT_ID    (new — needed for Pages endpoints)

Dependencies: requests   (swap for httpx if that's what her other tools use)
"""

from __future__ import annotations
import os
import requests

CF_API = "https://api.cloudflare.com/client/v4"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}",
        "Content-Type": "application/json",
    }


def _account_id() -> str:
    return os.environ["CLOUDFLARE_ACCOUNT_ID"]


def _zone_id(domain: str) -> str:
    """Resolve a zone ID from a bare domain (e.g. 'clshoa.com')."""
    r = requests.get(f"{CF_API}/zones", headers=_headers(), params={"name": domain})
    r.raise_for_status()
    result = r.json().get("result", [])
    if not result:
        raise ValueError(f"No Cloudflare zone found for {domain}")
    return result[0]["id"]


# ---------------------------------------------------------------------------
# Redirect rules  (Rulesets API, http_request_dynamic_redirect phase)
# ---------------------------------------------------------------------------

def cloudflare_redirect(
    zone_domain: str,
    target_url: str,
    hostnames: list[str] | None = None,
    status_code: int = 301,
    preserve_path: bool = False,
    list_only: bool = False,
) -> dict:
    """
    Create or list single redirect rules for a zone.

    zone_domain   : the Cloudflare zone, e.g. "clshoa.com"
    target_url    : where to send traffic, e.g. "https://clshoa.org"
    hostnames     : hostnames that trigger the redirect.
                    Defaults to [zone_domain, "www."+zone_domain].
    status_code   : 301 (permanent) or 302 (temporary)
    preserve_path : True keeps the path (clshoa.com/docs -> clshoa.org/docs);
                    False sends everything to target_url's root.
    list_only     : if True, just return the current redirect rules.

    Requires token scope: Zone > Dynamic Redirect > Edit.
    """
    zid = _zone_id(zone_domain)
    ep = f"{CF_API}/zones/{zid}/rulesets/phases/http_request_dynamic_redirect/entrypoint"

    # Fetch existing rules (404 = no entrypoint yet)
    existing: list = []
    g = requests.get(ep, headers=_headers())
    if g.status_code == 200:
        existing = g.json()["result"].get("rules", []) or []

    if list_only:
        return {"zone": zone_domain, "rules": existing}

    hostnames = hostnames or [zone_domain, f"www.{zone_domain}"]
    expr = " or ".join(f'http.host eq "{h}"' for h in hostnames)

    if preserve_path:
        target = {"expression": f'concat("{target_url}", http.request.uri.path)'}
    else:
        target = {"value": target_url}

    new_rule = {
        "action": "redirect",
        "expression": f"({expr})",
        "enabled": True,
        "description": f"{zone_domain} -> {target_url}",
        "action_parameters": {
            "from_value": {
                "status_code": status_code,
                "target_url": target,
                "preserve_query_string": True,
            }
        },
    }

    r = requests.put(ep, headers=_headers(), json={"rules": existing + [new_rule]})
    r.raise_for_status()
    return {"created": new_rule["description"], "result": r.json()["result"]}


# ---------------------------------------------------------------------------
# Pages management
# ---------------------------------------------------------------------------

def cloudflare_pages(action: str, project: str | None = None,
                     domain: str | None = None) -> dict:
    """
    Manage existing Cloudflare Pages projects.

    action = "list"        -> list all Pages projects
    action = "add_domain"  -> attach `domain` to `project`
    action = "deploy"      -> trigger a new production deployment of `project`

    Requires token scope: Account > Cloudflare Pages > Edit.

    NOTE: creating a *new* GitHub-connected project is NOT here — that needs
    the one-time GitHub<->Cloudflare OAuth done in the browser. These actions
    are for managing projects that already exist.
    """
    acct = _account_id()
    base = f"{CF_API}/accounts/{acct}/pages/projects"

    if action == "list":
        r = requests.get(base, headers=_headers())
        r.raise_for_status()
        return {"projects": [p["name"] for p in r.json()["result"]]}

    if action == "add_domain":
        r = requests.post(f"{base}/{project}/domains",
                          headers=_headers(), json={"name": domain})
        r.raise_for_status()
        return {"project": project, "domain_added": domain}

    if action == "deploy":
        r = requests.post(f"{base}/{project}/deployments", headers=_headers())
        r.raise_for_status()
        return {"project": project, "deployment": r.json()["result"]["id"]}

    raise ValueError(f"Unknown action: {action}")


# ---------------------------------------------------------------------------
# Tool schemas for Clawdia's Claude tool-calling layer
# (adapt field names to match her existing schema/dispatcher style)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "cloudflare_redirect",
        "description": "Create or list single redirect rules for a Cloudflare "
                       "zone (e.g. redirect clshoa.com -> clshoa.org).",
        "input_schema": {
            "type": "object",
            "properties": {
                "zone_domain": {"type": "string"},
                "target_url": {"type": "string"},
                "hostnames": {"type": "array", "items": {"type": "string"}},
                "status_code": {"type": "integer", "enum": [301, 302]},
                "preserve_path": {"type": "boolean"},
                "list_only": {"type": "boolean"},
            },
            "required": ["zone_domain", "target_url"],
        },
    },
    {
        "name": "cloudflare_pages",
        "description": "Manage existing Cloudflare Pages projects: list, "
                       "add a custom domain, or trigger a deployment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "add_domain", "deploy"]},
                "project": {"type": "string"},
                "domain": {"type": "string"},
            },
            "required": ["action"],
        },
    },
]
