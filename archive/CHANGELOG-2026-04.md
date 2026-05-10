# Clawdia Changelog

## April 12, 2026

### Emergency Recovery
- bot.py found empty (0 bytes) after failed incremental patch attempts
- All 30 memories in SQLite database were intact
- Full bot redeployed from scratch

### Root Cause
- Incremental patching via heredoc/string replacement causes corruption
- Fix: always full file rewrites, always validate with py_compile before restart

### New Features
- **File reading** — PDF, DOCX, XLSX, CSV, TXT via Telegram file upload
- **OCR support** — Tesseract + pdf2image for scanned/image PDFs
- **URL fetcher** — fetch_url tool reads any webpage

### GitHub Backup
- Git initialized in /opt/clawdia/
- Remote: github.com/seandurgin/openclaw-brain (clawdia branch)
- Daily cron at 2 AM UTC pushes bot.py, briefing.py, memory export

### Tool Count: 21
save_memory, delete_memory, web_search, fetch_url,
gmail_unread, gmail_read, gmail_send,
family_gmail_unread, family_gmail_read, family_gmail_send,
calendar_upcoming, calendar_add, drive_search, contacts_search,
onenote_notebooks, onenote_sections, onenote_list_pages,
onenote_recent, onenote_search, onenote_read, onenote_create

### Server Status
- Droplet: 209.38.49.104 (DigitalOcean NYC3)
- Memory: 30 entries intact
- Backup: clawdia branch, daily 02:00 UTC
