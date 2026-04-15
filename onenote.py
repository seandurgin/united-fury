import json, logging, re, requests, msal

log = logging.getLogger("clawdia.onenote")

CLIENT_ID     = "10fd6347-d39f-40cd-bbff-51a8c2af8471"
CLIENT_SECRET = "_pj8Q~Y-8sw~D73Y9u03ym1iwzl0IIoVbmi4Gdlu"
AUTHORITY     = "https://login.microsoftonline.com/consumers"
SCOPES        = ["Notes.ReadWrite", "Mail.Read", "Calendars.Read", "User.Read"]
TOKEN_FILE    = "/etc/clawdia/ms_token.json"
GRAPH_BASE    = "https://graph.microsoft.com/v1.0"

def get_access_token():
    with open(TOKEN_FILE) as f:
        token_data = json.load(f)
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
    result = None
    if "refresh_token" in token_data:
        result = app.acquire_token_by_refresh_token(
            token_data["refresh_token"], scopes=SCOPES
        )
    if not result or "access_token" not in result:
        raise Exception("Could not refresh Microsoft token.")
    token_data.update(result)
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)
    return result["access_token"]

def graph_get(path, params=None):
    token = get_access_token()
    r = requests.get(
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params, timeout=15
    )
    r.raise_for_status()
    return r.json()

def onenote_list_notebooks():
    try:
        data = graph_get("/me/onenote/notebooks")
        notebooks = data.get("value", [])
        if not notebooks:
            return "No OneNote notebooks found."
        lines = ["Your OneNote notebooks:"]
        for nb in notebooks:
            lines.append(f"- {nb['displayName']} (ID: {nb['id']})")
        return "\n".join(lines)
    except Exception as e:
        return f"OneNote error: {e}"

def onenote_list_sections(notebook_name=None):
    try:
        if notebook_name:
            data = graph_get("/me/onenote/notebooks")
            notebooks = data.get("value", [])
            nb = next((n for n in notebooks if notebook_name.lower() in n["displayName"].lower()), None)
            if not nb:
                return f"Notebook not found: {notebook_name}"
            data = graph_get(f"/me/onenote/notebooks/{nb['id']}/sections")
        else:
            data = graph_get("/me/onenote/sections")
        sections = data.get("value", [])
        if not sections:
            return "No sections found."
        lines = ["Sections:"]
        for s in sections:
            lines.append(f"- {s['displayName']} (ID: {s['id']})")
        return "\n".join(lines)
    except Exception as e:
        return f"OneNote error: {e}"

def onenote_recent_pages(max_results=10):
    try:
        data = graph_get("/me/onenote/pages", params={
            "$top": max_results,
            "$orderby": "lastModifiedDateTime desc",
            "$select": "title,lastModifiedDateTime,parentSection,id"
        })
        pages = data.get("value", [])
        if not pages:
            return "No recent OneNote pages found."
        lines = [f"Recent OneNote pages ({len(pages)}):"]
        for p in pages:
            section = p.get("parentSection", {}).get("displayName", "?")
            modified = p.get("lastModifiedDateTime", "?")[:10]
            lines.append(f"- {p['title']} [{section}] - {modified} (ID: {p['id']})")
        return "\n".join(lines)
    except Exception as e:
        return f"OneNote error: {e}"

def onenote_search(query, max_results=5):
    try:
        data = graph_get("/me/onenote/pages", params={
            "$top": max_results,
            "$search": query,
            "$select": "title,lastModifiedDateTime,parentSection,id"
        })
        pages = data.get("value", [])
        if not pages:
            return f"No OneNote pages found matching: {query}"
        lines = [f"OneNote pages matching {query}:"]
        for p in pages:
            section = p.get("parentSection", {}).get("displayName", "?")
            modified = p.get("lastModifiedDateTime", "?")[:10]
            lines.append(f"- {p['title']} [{section}] - {modified} (ID: {p['id']})")
        return "\n".join(lines)
    except Exception as e:
        return f"OneNote search error: {e}"

def onenote_get_page(page_id):
    try:
        token = get_access_token()
        r = requests.get(
            f"{GRAPH_BASE}/me/onenote/pages/{page_id}/content",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15
        )
        r.raise_for_status()
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:3000]
    except Exception as e:
        return f"Error reading page: {e}"

def onenote_create_page(section_id, title, content):
    try:
        token = get_access_token()
        html = f"""<!DOCTYPE html><html><head><title>{title}</title></head><body><h1>{title}</h1><p>{content}</p></body></html>"""
        r = requests.post(
            f"{GRAPH_BASE}/me/onenote/sections/{section_id}/pages",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/xhtml+xml"},
            data=html.encode("utf-8"), timeout=15
        )
        r.raise_for_status()
        return f"Page created: {title}"
    except Exception as e:
        return f"Failed to create page: {e}"
