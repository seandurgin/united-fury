"""Google Contacts helper for Clawdia."""
import logging
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

log = logging.getLogger("clawdia.contacts")

GOOGLE_TOKEN = "/etc/clawdia/google_token.json"
SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/contacts.readonly',
]

def get_creds():
    creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(GOOGLE_TOKEN, 'w') as f:
            f.write(creds.to_json())
    return creds

def contacts_search(query, max_results=5):
    try:
        creds = get_creds()
        service = build('people', 'v1', credentials=creds)
        results = service.people().searchContacts(
            query=query,
            readMask='names,emailAddresses,phoneNumbers,organizations',
            pageSize=max_results
        ).execute()
        people = results.get('results', [])
        if not people:
            return f"No contacts found matching: {query}"
        lines = [f"Contacts matching '{query}':"]
        for p in people:
            person = p.get('person', {})
            names = person.get('names', [{}])
            name = names[0].get('displayName', 'Unknown') if names else 'Unknown'
            emails = [e['value'] for e in person.get('emailAddresses', [])]
            phones = [p['value'] for p in person.get('phoneNumbers', [])]
            orgs   = [o.get('name','') for o in person.get('organizations', [])]
            lines.append(f"\n👤 {name}")
            if emails: lines.append(f"   📧 {', '.join(emails)}")
            if phones: lines.append(f"   📱 {', '.join(phones)}")
            if orgs:   lines.append(f"   🏢 {', '.join(orgs)}")
        return "\n".join(lines)
    except Exception as e:
        log.error("Contacts search error: %s", e)
        return f"Contacts error: {e}"

def contacts_list(max_results=20):
    try:
        creds = get_creds()
        service = build('people', 'v1', credentials=creds)
        results = service.people().connections().list(
            resourceName='people/me',
            pageSize=max_results,
            personFields='names,emailAddresses,phoneNumbers'
        ).execute()
        people = results.get('connections', [])
        if not people:
            return "No contacts found."
        lines = [f"Contacts ({len(people)}):"]
        for person in people:
            names  = person.get('names', [{}])
            name   = names[0].get('displayName', 'Unknown') if names else 'Unknown'
            emails = [e['value'] for e in person.get('emailAddresses', [])]
            phones = [p['value'] for p in person.get('phoneNumbers', [])]
            line   = f"  {name}"
            if emails: line += f" — {emails[0]}"
            if phones: line += f" — {phones[0]}"
            lines.append(line)
        return "\n".join(lines)
    except Exception as e:
        log.error("Contacts list error: %s", e)
        return f"Contacts error: {e}"
