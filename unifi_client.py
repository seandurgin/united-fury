"""UniFi Site Manager API wrapper for Clawdia.
Read-only, hits api.ui.com over HTTPS with X-API-KEY auth.
Site Manager API tier on Sean's account exposes: hosts, host detail, sites
(with aggregate stats), and a flat devices list. Per-client data is NOT
available on this tier (404 on /sites/{id}/clients endpoints).
"""
import os
import logging
import requests

log = logging.getLogger("clawdia.unifi")

UNIFI_API_BASE = "https://api.ui.com/v1"
UNIFI_API_KEY = os.environ.get("UNIFI_API_KEY", "")
UNIFI_TIMEOUT = 15  # seconds


def _unifi_get(path):
    """GET against the Site Manager API. Returns parsed JSON or raises."""
    if not UNIFI_API_KEY:
        raise RuntimeError("UNIFI_API_KEY not set in /etc/clawdia/env")
    url = UNIFI_API_BASE + path
    r = requests.get(
        url,
        headers={"X-API-KEY": UNIFI_API_KEY, "Accept": "application/json"},
        timeout=UNIFI_TIMEOUT,
    )
    if r.status_code == 401:
        raise RuntimeError("UniFi API key rejected (401). Regenerate at unifi.ui.com.")
    if r.status_code == 429:
        retry = r.headers.get("Retry-After", "?")
        raise RuntimeError(f"UniFi API rate limited (429). Retry after {retry}s.")
    if r.status_code != 200:
        raise RuntimeError(f"UniFi API {r.status_code}: {r.text[:200]}")
    return r.json()


def list_hosts():
    """Return list of UniFi consoles on the account."""
    data = _unifi_get("/hosts")
    return data.get("data", [])


def get_host_detail(host_id):
    """Return detailed state for a single host (UDM SE etc.).
    Includes reportedState with WAN info, internet issues, firmware, etc.
    """
    data = _unifi_get(f"/hosts/{host_id}")
    return data.get("data", data)


def list_sites():
    """Return sites with aggregate statistics (offline counts, client counts, etc.)."""
    data = _unifi_get("/sites")
    return data.get("data", [])


def list_devices():
    """Return flat list of all managed UniFi devices across all hosts.
    Response wraps devices in per-host arrays; flatten for simplicity.
    """
    data = _unifi_get("/devices")
    out = []
    for host_block in data.get("data", []):
        host_name = host_block.get("hostName", "?")
        for device in host_block.get("devices", []):
            device["_hostName"] = host_name
            out.append(device)
    return out
