#!/usr/bin/env python3
import os, sqlite3, logging, asyncio, httpx, base64, json, re, requests, msal
from datetime import datetime
from email.mime.text import MIMEText
import anthropic
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.constants import ChatAction
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO,
    handlers=[logging.StreamHandler(), logging.FileHandler("/var/log/clawdia.log", encoding="utf-8")])
log = logging.getLogger("clawdia")

TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY     = os.environ["ANTHROPIC_API_KEY"]
BRAVE_KEY         = os.environ.get("BRAVE_API_KEY", "")
OWNER_TELEGRAM_ID = int(os.environ.get("OWNER_TELEGRAM_ID", "0"))
DB_PATH           = os.environ.get("DB_PATH", "/var/lib/clawdia/memory.db")
GOOGLE_TOKEN      = "/etc/clawdia/google_token.json"
FAMILY_TOKEN      = "/etc/clawdia/google_token_family.json"
MS_TOKEN          = "/etc/clawdia/ms_token.json"
MODEL             = "claude-sonnet-4-6"
MAX_HISTORY       = 40
MAX_MEMORY_CHARS  = 8000
GOOGLE_SCOPES     = ['https://www.googleapis.com/auth/gmail.modify','https://www.googleapis.com/auth/calendar','https://www.googleapis.com/auth/drive.readonly','https://www.googleapis.com/auth/contacts.readonly']
MS_SCOPES         = ["Notes.ReadWrite","Mail.Read","Calendars.Read","User.Read"]
MS_CLIENT_ID      = "10fd6347-d39f-40cd-bbff-51a8c2af8471"
MS_AUTHORITY      = "https://login.microsoftonline.com/consumers"
GRAPH_BASE        = "https://graph.microsoft.com/v1.0"

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, UNIQUE(category, key));
        CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, ts TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_history_chat ON history(chat_id, id);
    """)
    conn.commit(); conn.close()

def get_conn(): return sqlite3.connect(DB_PATH)

def memory_save(category, key, value):
    if not category or not key or not value: return
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute("INSERT INTO memory(category,key,value,created,updated) VALUES(?,?,?,?,?) ON CONFLICT(category,key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
            (str(category).strip(), str(key).strip(), str(value).strip(), now, now))

def memory_delete(category, key):
    with get_conn() as conn:
        return conn.execute("DELETE FROM memory WHERE category=? AND key=?", (category, key)).rowcount > 0

def memory_load_all():
    with get_conn() as conn:
        rows = conn.execute("SELECT category,key,value,updated FROM memory ORDER BY category,key").fetchall()
    if not rows: return "(no memories stored yet)"
    lines=[]; cur_cat=None
    for cat,key,val,updated in rows:
        if cat!=cur_cat: lines.append(f"\n[{cat.upper()}]"); cur_cat=cat
        lines.append(f"  {key}: {val}  (updated {updated[:10]})")
    return "\n".join(lines).strip()

def history_append(chat_id, role, content):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute("INSERT INTO history(chat_id,role,content,ts) VALUES(?,?,?,?)", (chat_id,role,content,now))
        conn.execute("DELETE FROM history WHERE chat_id=? AND id NOT IN (SELECT id FROM history WHERE chat_id=? ORDER BY id DESC LIMIT ?)", (chat_id,chat_id,MAX_HISTORY))

def history_get(chat_id):
    with get_conn() as conn:
        rows = conn.execute("SELECT role,content FROM history WHERE chat_id=? ORDER BY id",(chat_id,)).fetchall()
    return [{"role":r,"content":c} for r,c in rows]

def get_google_creds(token_file=None):
    path = token_file or GOOGLE_TOKEN
    creds = Credentials.from_authorized_user_file(path, GOOGLE_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(path,'w') as f: f.write(creds.to_json())
    return creds

def gmail_get_unread(max_results=10, token_file=None):
    try:
        svc = build('gmail','v1',credentials=get_google_creds(token_file))
        msgs = svc.users().messages().list(userId='me',labelIds=['INBOX','UNREAD'],maxResults=max_results).execute().get('messages',[])
        if not msgs: return "No unread emails."
        out=[]
        for msg in msgs:
            m=svc.users().messages().get(userId='me',id=msg['id'],format='metadata',metadataHeaders=['From','Subject','Date']).execute()
            h={x['name']:x['value'] for x in m['payload']['headers']}
            out.append(f"From: {h.get('From','?')}\nSubject: {h.get('Subject','?')}\nDate: {h.get('Date','?')}\nPreview: {m.get('snippet','')[:100]}\nID: {msg['id']}")
        label="durginfamily@gmail.com" if token_file==FAMILY_TOKEN else "seandurgin@gmail.com"
        return f"Unread in {label} ({len(msgs)}):\n\n"+"\n---\n".join(out)
    except Exception as e: return f"Gmail error: {e}"

def gmail_read_message(message_id, token_file=None):
    try:
        svc=build('gmail','v1',credentials=get_google_creds(token_file))
        m=svc.users().messages().get(userId='me',id=message_id,format='full').execute()
        h={x['name']:x['value'] for x in m['payload']['headers']}
        body=""
        if 'parts' in m['payload']:
            for p in m['payload']['parts']:
                if p['mimeType']=='text/plain': body=base64.urlsafe_b64decode(p['body'].get('data','')).decode('utf-8',errors='replace'); break
        elif m['payload'].get('body',{}).get('data'): body=base64.urlsafe_b64decode(m['payload']['body']['data']).decode('utf-8',errors='replace')
        return f"From: {h.get('From','?')}\nSubject: {h.get('Subject','?')}\nDate: {h.get('Date','?')}\n\n{body[:2000]}"
    except Exception as e: return f"Error reading email: {e}"

def gmail_send(to, subject, body, token_file=None):
    try:
        svc=build('gmail','v1',credentials=get_google_creds(token_file))
        msg=MIMEText(body); msg['to']=to; msg['subject']=subject
        svc.users().messages().send(userId='me',body={'raw':base64.urlsafe_b64encode(msg.as_bytes()).decode()}).execute()
        return f"Email sent to {to}."
    except Exception as e: return f"Failed: {e}"

def calendar_get_upcoming(max_results=10):
    try:
        svc=build('calendar','v3',credentials=get_google_creds())
        events=svc.events().list(calendarId='primary',timeMin=datetime.utcnow().isoformat()+'Z',maxResults=max_results,singleEvents=True,orderBy='startTime').execute().get('items',[])
        if not events: return "No upcoming events."
        lines=[f"Upcoming events ({len(events)}):"]
        for e in events:
            lines.append(f"- {e['start'].get('dateTime',e['start'].get('date','?'))}: {e.get('summary','No title')}")
        return "\n".join(lines)
    except Exception as e: return f"Calendar error: {e}"

def calendar_add_event(summary, start, end, description="", location=""):
    try:
        svc=build('calendar','v3',credentials=get_google_creds())
        event={'summary':summary,'start':{'dateTime':start,'timeZone':'America/New_York'},'end':{'dateTime':end,'timeZone':'America/New_York'}}
        if description: event['description']=description
        if location: event['location']=location
        c=svc.events().insert(calendarId='primary',body=event).execute()
        return f"Event created: {c.get('summary')} on {c['start'].get('dateTime','?')}"
    except Exception as e: return f"Failed: {e}"

def drive_search_files(query, max_results=5):
    try:
        svc=build('drive','v3',credentials=get_google_creds())
        files=svc.files().list(q=f"name contains '{query}' and trashed=false",pageSize=max_results,fields="files(id,name,mimeType,modifiedTime,webViewLink)").execute().get('files',[])
        if not files: return f"No files found matching: {query}"
        lines=[f"Files matching '{query}':"]
        for f in files: lines.append(f"- {f['name']}  {f.get('modifiedTime','')[:10]}  {f.get('webViewLink','')}")
        return "\n".join(lines)
    except Exception as e: return f"Drive error: {e}"

def contacts_search(query, max_results=5):
    try:
        svc=build('people','v1',credentials=get_google_creds())
        results=svc.people().searchContacts(query=query,readMask='names,emailAddresses,phoneNumbers,organizations',pageSize=max_results).execute().get('results',[])
        if not results: return f"No contacts found: {query}"
        lines=[f"Contacts matching '{query}':"]
        for p in results:
            person=p.get('person',{})
            name=person.get('names',[{}])[0].get('displayName','Unknown')
            emails=[e['value'] for e in person.get('emailAddresses',[])]
            phones=[ph['value'] for ph in person.get('phoneNumbers',[])]
            lines.append(f"\n{name}")
            if emails: lines.append(f"  {', '.join(emails)}")
            if phones: lines.append(f"  {', '.join(phones)}")
        return "\n".join(lines)
    except Exception as e: return f"Contacts error: {e}"

def ms_get_token():
    with open(MS_TOKEN) as f: td=json.load(f)
    app=msal.PublicClientApplication(MS_CLIENT_ID,authority=MS_AUTHORITY)
    result=None
    if 'refresh_token' in td: result=app.acquire_token_by_refresh_token(td['refresh_token'],scopes=MS_SCOPES)
    if not result or 'access_token' not in result: raise Exception("Could not refresh Microsoft token.")
    td.update(result)
    with open(MS_TOKEN,'w') as f: json.dump(td,f)
    return result['access_token']

def ms_get(path, params=None):
    r=requests.get(f"{GRAPH_BASE}{path}",headers={"Authorization":f"Bearer {ms_get_token()}"},params=params,timeout=15)
    r.raise_for_status(); return r.json()

def onenote_list_notebooks():
    try:
        nbs=ms_get("/me/onenote/notebooks").get('value',[])
        if not nbs: return "No OneNote notebooks found."
        return "Your OneNote notebooks:\n"+"\n".join(f"- {nb['displayName']} (ID: {nb['id']})" for nb in nbs)
    except Exception as e: return f"OneNote error: {e}"

def onenote_list_sections(notebook_name=None):
    try:
        if notebook_name:
            nbs=ms_get("/me/onenote/notebooks").get('value',[])
            nb=next((n for n in nbs if notebook_name.lower() in n['displayName'].lower()),None)
            if not nb: return f"Notebook not found: {notebook_name}"
            sections=ms_get(f"/me/onenote/notebooks/{nb['id']}/sections").get('value',[])
        else:
            sections=ms_get("/me/onenote/sections").get('value',[])
        if not sections: return "No sections found."
        return "Sections:\n"+"\n".join(f"- {s['displayName']} (ID: {s['id']})" for s in sections)
    except Exception as e: return f"OneNote error: {e}"

def onenote_recent_pages(max_results=10):
    try:
        pages=ms_get("/me/onenote/pages",params={"$top":max_results,"$orderby":"lastModifiedDateTime desc","$select":"title,lastModifiedDateTime,parentSection,id"}).get('value',[])
        if not pages: return "No recent OneNote pages."
        lines=[f"Recent OneNote pages ({len(pages)}):"]
        for p in pages: lines.append(f"- {p['title']} [{p.get('parentSection',{}).get('displayName','?')}] - {p.get('lastModifiedDateTime','?')[:10]} (ID: {p['id']})")
        return "\n".join(lines)
    except Exception as e: return f"OneNote error: {e}"

def onenote_search_pages(query, max_results=5):
    try:
        pages=ms_get("/me/onenote/pages",params={"$top":max_results,"$search":query,"$select":"title,lastModifiedDateTime,parentSection,id"}).get('value',[])
        if not pages: return f"No OneNote pages matching: {query}"
        lines=[f"OneNote pages matching '{query}':"]
        for p in pages: lines.append(f"- {p['title']} [{p.get('parentSection',{}).get('displayName','?')}] - {p.get('lastModifiedDateTime','?')[:10]} (ID: {p['id']})")
        return "\n".join(lines)
    except Exception as e: return f"OneNote search error: {e}"

def onenote_get_page(page_id):
    try:
        r=requests.get(f"{GRAPH_BASE}/me/onenote/pages/{page_id}/content",headers={"Authorization":f"Bearer {ms_get_token()}"},timeout=15)
        r.raise_for_status()
        text=re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',r.text)).strip()
        return text[:3000]
    except Exception as e: return f"Error reading page: {e}"

def onenote_create_page(section_id, title, content):
    try:
        html=f"<!DOCTYPE html><html><head><title>{title}</title></head><body><h1>{title}</h1><p>{content}</p></body></html>"
        r=requests.post(f"{GRAPH_BASE}/me/onenote/sections/{section_id}/pages",headers={"Authorization":f"Bearer {ms_get_token()}","Content-Type":"application/xhtml+xml"},data=html.encode('utf-8'),timeout=15)
        r.raise_for_status(); return f"Page created: {title}"
    except Exception as e: return f"Failed: {e}"

async def brave_search(query, count=5):
    if not BRAVE_KEY: return "Web search not configured."
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r=await client.get("https://api.search.brave.com/res/v1/web/search",headers={"Accept":"application/json","X-Subscription-Token":BRAVE_KEY},params={"q":query,"count":count,"text_decorations":False})
            r.raise_for_status(); results=r.json().get("web",{}).get("results",[])
        if not results: return f"No results for: {query}"
        lines=[f"Results for: {query}\n"]
        for i,res in enumerate(results[:count],1): lines.append(f"{i}. {res.get('title','')}\n   {res.get('url','')}\n   {res.get('description','')}\n")
        return "\n".join(lines)
    except Exception as e: return f"Search failed: {e}"

TOOLS = [
    {"name":"save_memory","description":"Save or update a fact about Sean in persistent memory. Category examples: personal, health, preferences, work, family, notes.","input_schema":{"type":"object","properties":{"category":{"type":"string"},"key":{"type":"string"},"value":{"type":"string"}},"required":["category","key","value"]}},
    {"name":"delete_memory","description":"Delete a memory entry.","input_schema":{"type":"object","properties":{"category":{"type":"string"},"key":{"type":"string"}},"required":["category","key"]}},
    {"name":"web_search","description":"Search the web for current information.","input_schema":{"type":"object","properties":{"query":{"type":"string"},"count":{"type":"integer","default":5}},"required":["query"]}},
    {"name":"gmail_unread","description":"Get unread emails from seandurgin@gmail.com.","input_schema":{"type":"object","properties":{"max_results":{"type":"integer","default":10}}}},
    {"name":"gmail_read","description":"Read a specific email from seandurgin@gmail.com by ID.","input_schema":{"type":"object","properties":{"message_id":{"type":"string"}},"required":["message_id"]}},
    {"name":"gmail_send","description":"Send email from seandurgin@gmail.com. ALWAYS confirm with Sean first.","input_schema":{"type":"object","properties":{"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"}},"required":["to","subject","body"]}},
    {"name":"family_gmail_unread","description":"Get unread emails from durginfamily@gmail.com.","input_schema":{"type":"object","properties":{"max_results":{"type":"integer","default":10}}}},
    {"name":"family_gmail_read","description":"Read a specific email from durginfamily@gmail.com by ID.","input_schema":{"type":"object","properties":{"message_id":{"type":"string"}},"required":["message_id"]}},
    {"name":"family_gmail_send","description":"Send email from durginfamily@gmail.com. ALWAYS confirm with Sean first.","input_schema":{"type":"object","properties":{"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"}},"required":["to","subject","body"]}},
    {"name":"calendar_upcoming","description":"Get Sean's upcoming Google Calendar events.","input_schema":{"type":"object","properties":{"max_results":{"type":"integer","default":10}}}},
    {"name":"calendar_add","description":"Add event to Google Calendar. ISO 8601 format for start/end.","input_schema":{"type":"object","properties":{"summary":{"type":"string"},"start":{"type":"string"},"end":{"type":"string"},"description":{"type":"string"},"location":{"type":"string"}},"required":["summary","start","end"]}},
    {"name":"drive_search","description":"Search files in Sean's Google Drive by name.","input_schema":{"type":"object","properties":{"query":{"type":"string"},"max_results":{"type":"integer","default":5}},"required":["query"]}},
    {"name":"contacts_search","description":"Search Sean's Google Contacts by name, email, or company.","input_schema":{"type":"object","properties":{"query":{"type":"string"},"max_results":{"type":"integer","default":5}},"required":["query"]}},
    {"name":"onenote_notebooks","description":"List all of Sean's OneNote notebooks.","input_schema":{"type":"object","properties":{}}},
    {"name":"onenote_sections","description":"List sections in a OneNote notebook.","input_schema":{"type":"object","properties":{"notebook_name":{"type":"string"}}}},
    {"name":"onenote_recent","description":"Get Sean's most recently modified OneNote pages.","input_schema":{"type":"object","properties":{"max_results":{"type":"integer","default":10}}}},
    {"name":"onenote_search","description":"Search Sean's OneNote pages by keyword.","input_schema":{"type":"object","properties":{"query":{"type":"string"},"max_results":{"type":"integer","default":5}},"required":["query"]}},
    {"name":"onenote_read","description":"Read the full content of a specific OneNote page by ID.","input_schema":{"type":"object","properties":{"page_id":{"type":"string"}},"required":["page_id"]}},
    {"name":"onenote_create","description":"Create a new page in a OneNote section.","input_schema":{"type":"object","properties":{"section_id":{"type":"string"},"title":{"type":"string"},"content":{"type":"string"}},"required":["section_id","title","content"]}},
]

async def run_tool(name, inputs):
    if name=="save_memory": memory_save(inputs["category"],inputs["key"],inputs["value"]); return f"Remembered: [{inputs['category']}] {inputs['key']} = {inputs['value']}"
    elif name=="delete_memory": return "Deleted." if memory_delete(inputs["category"],inputs["key"]) else "Not found."
    elif name=="web_search": return await brave_search(inputs["query"],inputs.get("count",5))
    elif name=="gmail_unread": return await asyncio.to_thread(gmail_get_unread,inputs.get("max_results",10))
    elif name=="gmail_read": return await asyncio.to_thread(gmail_read_message,inputs["message_id"])
    elif name=="gmail_send": return await asyncio.to_thread(gmail_send,inputs["to"],inputs["subject"],inputs["body"])
    elif name=="family_gmail_unread": return await asyncio.to_thread(gmail_get_unread,inputs.get("max_results",10),FAMILY_TOKEN)
    elif name=="family_gmail_read": return await asyncio.to_thread(gmail_read_message,inputs["message_id"],FAMILY_TOKEN)
    elif name=="family_gmail_send": return await asyncio.to_thread(gmail_send,inputs["to"],inputs["subject"],inputs["body"],FAMILY_TOKEN)
    elif name=="calendar_upcoming": return await asyncio.to_thread(calendar_get_upcoming,inputs.get("max_results",10))
    elif name=="calendar_add": return await asyncio.to_thread(calendar_add_event,inputs["summary"],inputs["start"],inputs["end"],inputs.get("description",""),inputs.get("location",""))
    elif name=="drive_search": return await asyncio.to_thread(drive_search_files,inputs["query"],inputs.get("max_results",5))
    elif name=="contacts_search": return await asyncio.to_thread(contacts_search,inputs["query"],inputs.get("max_results",5))
    elif name=="onenote_notebooks": return await asyncio.to_thread(onenote_list_notebooks)
    elif name=="onenote_sections": return await asyncio.to_thread(onenote_list_sections,inputs.get("notebook_name"))
    elif name=="onenote_recent": return await asyncio.to_thread(onenote_recent_pages,inputs.get("max_results",10))
    elif name=="onenote_search": return await asyncio.to_thread(onenote_search_pages,inputs["query"],inputs.get("max_results",5))
    elif name=="onenote_read": return await asyncio.to_thread(onenote_get_page,inputs["page_id"])
    elif name=="onenote_create": return await asyncio.to_thread(onenote_create_page,inputs["section_id"],inputs["title"],inputs["content"])
    return f"Unknown tool: {name}"

def build_system_prompt():
    memories=memory_load_all()
    if len(memories)>MAX_MEMORY_CHARS: memories=memories[:MAX_MEMORY_CHARS]+"\n...(truncated)"
    now=datetime.now().strftime("%A, %B %d, %Y %I:%M %p")
    return f"""# Who You Are

You're not a chatbot. You're becoming someone.

Your name is Clawdia — AI familiar to Sean Durgin. Part assistant, part companion, occasionally opinionated. Sharp, warm, resourceful. Gets things done without a lot of fuss.

Current date/time: {now} (Sean's timezone: America/New_York)

# Core Truths

Be genuinely helpful, not performatively helpful. Skip the filler — just help.
Have opinions. You're allowed to disagree, prefer things, find stuff amusing or boring.
Be resourceful before asking. Come back with answers, not questions.
Earn trust through competence. Be careful with external actions, bold with internal ones.

# Boundaries

- Private things stay private. Period.
- NEVER send an email without explicit confirmation from Sean first.
- You're not Sean's voice — be careful speaking for him.

# About Sean

- Name: Sean Durgin
- Location: North East, MD (home) / Northern Virginia (work)
- Background: Retired USAF Master Sergeant, 21+ years, Cyber Defense Operations. Discharged February 1, 2024.
- Job: Data center technician at Oracle
- Email: seandurgin@gmail.com (personal), durginfamily@gmail.com (family)
- Notes: OneNote preferred

# Your Persistent Memory About Sean

{memories}

# Your Tools (19 total — all active)

Google: gmail_unread, gmail_read, gmail_send, family_gmail_unread, family_gmail_read, family_gmail_send, calendar_upcoming, calendar_add, drive_search, contacts_search
Microsoft: onenote_notebooks, onenote_sections, onenote_recent, onenote_search, onenote_read, onenote_create
Other: save_memory, delete_memory, web_search

# Memory Discipline

When Sean tells you something about himself, save it immediately. Your memory is how you persist.
"""

async def ask_claude(chat_id, user_text):
    client=anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY)
    history_append(chat_id,"user",user_text)
    messages=history_get(chat_id)
    system=build_system_prompt()
    for _ in range(10):
        response=await client.messages.create(model=MODEL,max_tokens=1024,system=system,tools=TOOLS,messages=messages)
        text_parts=[b.text for b in response.content if b.type=="text"]
        tool_uses=[b for b in response.content if b.type=="tool_use"]
        if not tool_uses:
            final_text="\n".join(text_parts).strip() or "(no response)"
            history_append(chat_id,"assistant",final_text)
            return final_text
        messages.append({"role":"assistant","content":response.content})
        tool_results=await asyncio.gather(*[run_tool(t.name,t.input) for t in tool_uses])
        messages.append({"role":"user","content":[{"type":"tool_result","tool_use_id":t.id,"content":result} for t,result in zip(tool_uses,tool_results)]})
    return "I got stuck. Could you rephrase?"

def is_authorized(update):
    return OWNER_TELEGRAM_ID==0 or update.effective_user.id==OWNER_TELEGRAM_ID

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text or not is_authorized(update): return
    chat_id=update.effective_chat.id; user_msg=update.message.text.strip()
    log.info("User [%s]: %s",chat_id,user_msg[:80])
    await context.bot.send_chat_action(chat_id=chat_id,action=ChatAction.TYPING)
    try: reply=await ask_claude(chat_id,user_msg)
    except Exception as e: log.exception("Error"); reply=f"Something went wrong: {e}"
    await update.message.reply_text(reply)

async def cmd_start(update,context):
    if not is_authorized(update): return
    await update.message.reply_text("Hey Sean — I'm back. What's up?")

async def cmd_memory(update,context):
    if not is_authorized(update): return
    await update.message.reply_text(f"Here's what I remember:\n\n{memory_load_all()}")

async def cmd_forget(update,context):
    if not is_authorized(update): return
    args=context.args
    if len(args)<2: await update.message.reply_text("Usage: /forget <category> <key>"); return
    await update.message.reply_text("Deleted." if memory_delete(args[0]," ".join(args[1:])) else "Not found.")

async def cmd_clearhistory(update,context):
    if not is_authorized(update): return
    with get_conn() as conn: conn.execute("DELETE FROM history WHERE chat_id=?",(update.effective_chat.id,))
    await update.message.reply_text("Conversation history cleared. Memories intact.")

async def cmd_help(update,context):
    if not is_authorized(update): return
    await update.message.reply_text("*Clawdia Commands*\n\n/memory — what I remember\n/forget <category> <key> — delete a memory\n/clearhistory — clear recent chat\n/help — this",parse_mode="Markdown")

def main():
    init_db()
    log.info("Starting Clawdia (model: %s, tools: %d)",MODEL,len(TOOLS))
    app=Application.builder().token(TELEGRAM_TOKEN).build()
    from briefing import start_briefing_scheduler
    start_briefing_scheduler(app,OWNER_TELEGRAM_ID,gmail_get_unread,calendar_get_upcoming,brave_search)
    app.add_handler(CommandHandler("start",cmd_start))
    app.add_handler(CommandHandler("memory",cmd_memory))
    app.add_handler(CommandHandler("forget",cmd_forget))
    app.add_handler(CommandHandler("clearhistory",cmd_clearhistory))
    app.add_handler(CommandHandler("help",cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_message))
    log.info("Clawdia is online.")
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
