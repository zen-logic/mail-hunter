# Mail Hunter

> **Early release:** This project is under active development and has not been
> thoroughly tested. Back up your data. Do not use as your only copy of anything
> important.

Self-hosted email archiving. Sync IMAP servers, import EML and MBOX files,
search everything from one browser tab. Compressed, deduplicated, yours.

## Why Mail Hunter

Old IMAP servers you're afraid to close. MBOX exports from providers that no
longer exist. EML files rescued from old machines. Mail Hunter pulls them all
into one searchable, deduplicated, compressed archive on your own hardware --
before the next provider shuts down.

## Features

- **IMAP Sync** -- Incremental sync via UID checkpoints. Full resync and purge options. Configurable auto-sync intervals per server. Sync queue ensures one operation at a time. Progress streamed live over WebSocket.
- **Import** -- Drag and drop EML files or MBOX archives. Automatic address matching to existing servers. Batch import with duplicate detection.
- **Deduplication** -- Messages hashed by Message-ID and content. Identical messages across servers and folders stored once on disk. Duplicate badges in the message list. Click "Show Duplicates" to see every copy with its server and folder location.
- **Conversation Threading** -- Select any reply and click "Show Thread" to see the full conversation across servers and folders, reconstructed from In-Reply-To and References headers.
- **Compression** -- Raw EML files compressed with zstd at level 19. Content-addressable storage keyed by SHA-256.
- **Tagging** -- Tag individual messages or batch-tag entire selections. Gmail labels imported automatically via X-GM-LABELS backfill.
- **Legal Hold** -- Place messages on legal hold to prevent deletion. Held messages survive batch deletes, folder purges, and server removal.
- **Message Preview** -- View HTML (with inline CID images), plain text, and raw RFC822 source. Download the original EML or extract individual attachments.
- **Search** -- Full-text search across all servers, folders, and years. Filter by sender, recipient, subject, body, date range, attachment filename, tags, duplicate status, or legal hold. Save searches for one-click re-use.
- **Batch Operations** -- Select multiple messages with click, shift-click, or select-all. Batch delete, batch tag, batch hold, and batch export as zip. Legal hold respected across all bulk operations.
- **Dashboard** -- Global stats with per-server breakdown of message counts, duplicates, held messages, and storage. Click any server name to navigate directly.
- **Real-Time UI** -- Three-panel layout with resizable panes. Live sync progress, folder counts, and system status via WebSocket. Keyboard navigation and colour themes.
- **Encrypted Credentials** -- IMAP passwords encrypted at rest with Fernet symmetric encryption. Key generated on first run, stored in your config.

## Quick Start

```bash
pip install -r requirements.txt
python -m mail_hunter
```

Open http://localhost:8700 in your browser.

## Tech

Python. Starlette. SQLite (WAL mode). Vanilla JS. No build step, no framework
churn, no external services. Runs anywhere Python runs.

## Licence

MIT -- [Zen Logic Ltd.](https://zenlogic.co.uk)
