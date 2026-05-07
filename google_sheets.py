"""Google Sheets creation for Clawdia.

Single function `create_google_sheet(title, tabs)` that:
1. Creates a spreadsheet via Sheets API (multi-tab capable)
2. Populates each tab with header row + data rows (formulas work — cells starting with '=' are sent as USER_ENTERED so Sheets parses them)
3. Sets Drive sharing to anyone-with-link-can-edit
4. Returns the public URL

Requires the `spreadsheets` and `drive` scopes on the active creds. Both are
present in GOOGLE_SCOPES as of 2026-04-29 re-auth.
"""
import logging
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger("clawdia.gsheets")


def _format_header_row(svc, spreadsheet_id, sheet_id):
    """Bold + frozen first row + light blue background, matching create_spreadsheet's style."""
    requests = [
        # Freeze first row
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id,
                           "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"
        }},
        # Format header row
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.27, "green": 0.45, "blue": 0.77},
                "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1},
                               "bold": True}
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"
        }},
    ]
    svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests}
    ).execute()


def _share_anyone_can_edit(drive_svc, file_id):
    """Add an 'anyone with link can edit' permission. Idempotent if already shared."""
    drive_svc.permissions().create(
        fileId=file_id,
        body={"role": "writer", "type": "anyone"},
        fields="id"
    ).execute()


def create_google_sheet(title, tabs, get_creds_fn):
    """Create a Google Sheet with multiple tabs and formula support.

    Args:
        title: Spreadsheet title.
        tabs: List of dicts, each with:
            - name: str (tab name; 1+ required)
            - headers: list[str] (column headers)
            - rows: list[list] (data rows; matches header length)
        get_creds_fn: callable returning a Google Credentials object.

    Returns:
        str: spreadsheet URL on success, or "ERROR: ..." string on failure.
    """
    if not title or not isinstance(title, str):
        return "ERROR: create_google_sheet requires a non-empty 'title' string."
    if not tabs or not isinstance(tabs, list):
        return "ERROR: create_google_sheet requires a non-empty 'tabs' list."
    for i, tab in enumerate(tabs):
        if not isinstance(tab, dict):
            return f"ERROR: tab #{i+1} must be a dict with 'name', 'headers', 'rows'."
        if not tab.get("name"):
            return f"ERROR: tab #{i+1} is missing 'name'."
        if not tab.get("headers"):
            return f"ERROR: tab '{tab['name']}' is missing 'headers' (non-empty list required)."

    try:
        creds = get_creds_fn()
        sheets_svc = build("sheets", "v4", credentials=creds)
        drive_svc = build("drive", "v3", credentials=creds)

        # 1. Build the spreadsheet with all tabs at once. Sheet name is sanitized
        #    by Google itself for length/illegal chars but we'll trim to be safe.
        sheet_specs = []
        for tab in tabs:
            name = (tab["name"] or "Sheet1")[:100].strip() or "Sheet1"
            sheet_specs.append({"properties": {"title": name}})

        body = {
            "properties": {"title": title[:200]},
            "sheets": sheet_specs,
        }
        result = sheets_svc.spreadsheets().create(
            body=body,
            fields="spreadsheetId,sheets(properties(sheetId,title))"
        ).execute()
        sheet_id = result["spreadsheetId"]
        sheet_meta = {s["properties"]["title"]: s["properties"]["sheetId"]
                      for s in result["sheets"]}

        # 2. Populate each tab.
        data_payload = []
        for tab in tabs:
            name = (tab["name"] or "Sheet1")[:100].strip() or "Sheet1"
            headers = tab["headers"]
            rows = tab.get("rows", [])
            values = [headers] + rows
            data_payload.append({
                "range": f"'{name}'!A1",
                "values": values,
            })

        # USER_ENTERED makes Sheets parse formulas (=SUM(...)) and dates/numbers
        # the way a human typing into the UI would. RAW would store '=SUM(...)' as
        # literal text, defeating the formula request.
        sheets_svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "USER_ENTERED",
                  "data": data_payload}
        ).execute()

        # 3. Format header rows on every tab + freeze first row.
        for tab in tabs:
            name = (tab["name"] or "Sheet1")[:100].strip() or "Sheet1"
            sid = sheet_meta.get(name)
            if sid is None:
                continue
            try:
                _format_header_row(sheets_svc, sheet_id, sid)
            except HttpError as fe:
                log.warning("Header format failed for tab %s: %s", name, fe)

        # 4. Share: anyone with link can edit.
        try:
            _share_anyone_can_edit(drive_svc, sheet_id)
        except HttpError as se:
            log.warning("Public-share permission failed: %s", se)
            return (f"Sheet created but public sharing failed ({se}). "
                    f"URL (only you can view): https://docs.google.com/spreadsheets/d/{sheet_id}/edit")

        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        n_rows = sum(len(t.get("rows", [])) for t in tabs)
        n_tabs = len(tabs)
        log.info("create_google_sheet: %d tab(s), %d data row(s), id=%s", n_tabs, n_rows, sheet_id)
        return f"Created Google Sheet '{title}' ({n_tabs} tab(s), {n_rows} data row(s)). Anyone with link can edit: {url}"

    except HttpError as e:
        log.error("create_google_sheet HttpError: %s", e)
        return f"ERROR: Google API error ({e.resp.status if hasattr(e,'resp') else '?'}): {e}"
    except Exception as e:
        log.exception("create_google_sheet failed")
        return f"ERROR: {e}"
