"""Google API helpers for durginfamily@gmail.com account."""
import os, base64, logging
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

log = logging.getLogger("clawdia.family")

FAMILY_TOKEN = "/etc/clawdia/google_token_family.json"
SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/contacts.readonly',
]

def get_family_creds():
    creds = Credentials.from_authorized_user_file(FAMILY_TOKEN, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(FAMILY_TOKEN, 'w') as f:
            f.write(creds.to_json())
    return creds

def family_gmail_unread(max_results=10):
    try:
        creds = get_family_creds()
        service = build('gmail', 'v1', credentials=creds)
        results = service.users().messages().list(
            userId='me', labelIds=['INBOX', 'UNREAD'], maxResults=max_results
        ).execute()
        messages = results.get('messages', [])
        if not messages:
            return "No unread emails in durginfamily@gmail.com."
        summaries = []
        for msg in messages:
            m = service.users().messages().get(
                userId='me', id=msg['id'], format='metadata',
                metadataHeaders=['From', 'Subject', 'Date']
            ).execute()
            headers = {h['name']: h['value'] for h in m['payload']['headers']}
            snippet = m.get('snippet', '')[:100]
            summaries.append(
                f"From: {headers.get('From','?')}\n"
                f"Subject: {headers.get('Subject','?')}\n"
                f"Date: {headers.get('Date','?')}\n"
                f"Preview: {snippet}\n"
                f"ID: {msg['id']}"
            )
        return f"durginfamily@gmail.com unread ({len(messages)}):\n\n" + "\n---\n".join(summaries)
    except Exception as e:
        log.error("Family Gmail error: %s", e)
        return f"Family Gmail error: {e}"

def family_gmail_read(message_id):
    try:
        creds = get_family_creds()
        service = build('gmail', 'v1', credentials=creds)
        m = service.users().messages().get(
            userId='me', id=message_id, format='full'
        ).execute()
        headers = {h['name']: h['value'] for h in m['payload']['headers']}
        body = ""
        if 'parts' in m['payload']:
            for part in m['payload']['parts']:
                if part['mimeType'] == 'text/plain':
                    data = part['body'].get('data', '')
                    body = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
                    break
        elif 'body' in m['payload']:
            data = m['payload']['body'].get('data', '')
            if data:
                body = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
        return (
            f"[durginfamily@gmail.com]\n"
            f"From: {headers.get('From','?')}\n"
            f"Subject: {headers.get('Subject','?')}\n"
            f"Date: {headers.get('Date','?')}\n\n"
            f"{body[:2000]}"
        )
    except Exception as e:
        return f"Error reading family email: {e}"

def family_gmail_send(to, subject, body):
    try:
        creds = get_family_creds()
        service = build('gmail', 'v1', credentials=creds)
        msg = MIMEText(body)
        msg['to'] = to
        msg['subject'] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        return f"Email sent from durginfamily@gmail.com to {to}."
    except Exception as e:
        log.error("Family Gmail send error: %s", e)
        return f"Failed to send family email: {e}"
