# Mail Hunter

> **WARNING: This project is under active development and is NOT suitable for production deployment.**

Email archiving and search tool for managing remote mailboxes and local mail archives. This is not an email client — it is an archiving server only.

## Features

- Connect to IMAP servers and sync mailboxes incrementally
- Import EML and MBOX files
- Full-text search across From, To, Subject, Body, and Date
- HTML, plain text, and raw source message preview
- Attachment viewing and download
- Tagging system for organising messages
- Duplicate detection via content hashing
- Legal hold support to prevent accidental deletion
- Raw EML archiving to disk

## Requirements

- Python 3.11+
- SQLite 3.35+ (for DROP COLUMN support)

## Quick Start

```bash
pip install -r requirements.txt
uvicorn mail_hunter.app:app --reload
```

Open http://localhost:8000 in your browser.

## Licence

MIT
