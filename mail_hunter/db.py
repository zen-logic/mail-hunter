import aiosqlite
from pathlib import Path
from mail_hunter.config import load_config

_db = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 993,
    username TEXT NOT NULL,
    password TEXT NOT NULL DEFAULT '',
    use_ssl INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    UNIQUE(server_id, name)
);

CREATE TABLE IF NOT EXISTS mails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    uid TEXT,
    message_id TEXT,
    subject TEXT,
    from_name TEXT,
    from_addr TEXT,
    to_addr TEXT,
    date TEXT,
    size INTEGER,
    unread INTEGER NOT NULL DEFAULT 0,
    attachment_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mail_id INTEGER NOT NULL REFERENCES mails(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content_type TEXT,
    size INTEGER
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mail_id INTEGER NOT NULL REFERENCES mails(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    UNIQUE(mail_id, tag)
);
"""


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        config = load_config()
        db_path = Path(config.get("database", "mail_hunter.db"))
        if not db_path.is_absolute():
            db_path = Path(__file__).resolve().parent.parent / db_path
        _db = await aiosqlite.connect(db_path)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA foreign_keys=ON")
        await _db.execute("PRAGMA busy_timeout=30000")
        await init_db(_db)
    return _db


MIGRATIONS = [
    # mails columns
    ("mails", "content_hash", "ALTER TABLE mails ADD COLUMN content_hash TEXT"),
    (
        "mails",
        "dup_count",
        "ALTER TABLE mails ADD COLUMN dup_count INTEGER NOT NULL DEFAULT 0",
    ),
    ("mails", "body_text", "ALTER TABLE mails ADD COLUMN body_text TEXT"),
    ("mails", "raw_path", "ALTER TABLE mails ADD COLUMN raw_path TEXT"),
    ("mails", "raw_size", "ALTER TABLE mails ADD COLUMN raw_size INTEGER"),
    ("mails", "cc_addr", "ALTER TABLE mails ADD COLUMN cc_addr TEXT"),
    ("mails", "reply_to", "ALTER TABLE mails ADD COLUMN reply_to TEXT"),
    ("mails", "in_reply_to", "ALTER TABLE mails ADD COLUMN in_reply_to TEXT"),
    (
        "mails",
        "references_header",
        "ALTER TABLE mails ADD COLUMN references_header TEXT",
    ),
    (
        "mails",
        "legal_hold",
        "ALTER TABLE mails ADD COLUMN legal_hold INTEGER NOT NULL DEFAULT 0",
    ),
    # servers columns
    (
        "servers",
        "protocol",
        "ALTER TABLE servers ADD COLUMN protocol TEXT NOT NULL DEFAULT 'imap'",
    ),
    ("servers", "last_sync", "ALTER TABLE servers ADD COLUMN last_sync TEXT"),
    (
        "servers",
        "sync_enabled",
        "ALTER TABLE servers ADD COLUMN sync_enabled INTEGER NOT NULL DEFAULT 1",
    ),
    (
        "servers",
        "syncing",
        "ALTER TABLE servers ADD COLUMN syncing INTEGER NOT NULL DEFAULT 0",
    ),
    # drop unused columns
    ("mails", "body_preview", "ALTER TABLE mails DROP COLUMN body_preview"),
    (
        "servers",
        "is_gmail",
        "ALTER TABLE servers ADD COLUMN is_gmail INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "servers",
        "sync_interval",
        "ALTER TABLE servers ADD COLUMN sync_interval INTEGER NOT NULL DEFAULT 15",
    ),
    # attachments columns
    (
        "attachments",
        "content_hash",
        "ALTER TABLE attachments ADD COLUMN content_hash TEXT",
    ),
]

SYNC_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS sync_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    folder_name TEXT NOT NULL,
    uid_validity INTEGER,
    last_uid INTEGER NOT NULL DEFAULT 0,
    last_sync TEXT,
    UNIQUE(server_id, folder_name)
)
"""

SYNC_QUEUE_TABLE = """
CREATE TABLE IF NOT EXISTS sync_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    folder TEXT,
    full INTEGER NOT NULL DEFAULT 0,
    purge INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(server_id)
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_mails_server_id ON mails(server_id)",
    "CREATE INDEX IF NOT EXISTS idx_mails_message_id ON mails(message_id)",
    "CREATE INDEX IF NOT EXISTS idx_mails_date ON mails(date)",
    "CREATE INDEX IF NOT EXISTS idx_mails_content_hash ON mails(content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_tags_mail_id ON tags(mail_id)",
    "CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag)",
]


async def init_db(db: aiosqlite.Connection):
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    for statement in SCHEMA.split(";"):
        stmt = statement.strip()
        if stmt:
            await db.execute(stmt)
    # Additive migrations
    for _table, _col, sql in MIGRATIONS:
        try:
            await db.execute(sql)
        except Exception:
            pass  # column already exists
    await db.execute(SYNC_STATE_TABLE)
    await db.execute(SYNC_QUEUE_TABLE)
    for idx_sql in INDEXES:
        await db.execute(idx_sql)
    await db.commit()


async def close_db():
    global _db
    if _db is not None:
        await _db.close()
        _db = None


# ---------------------------------------------------------------------------
# Cooperative write-lock yielding (same pattern as File Hunter)
# ---------------------------------------------------------------------------
# Background tasks (sync, import) hold the write lock for extended periods.
# UI operations signal that they need the lock; background tasks check this
# flag after each commit and yield briefly so the UI write can proceed.
# ---------------------------------------------------------------------------

_write_lock_requested = False


def request_write_lock():
    """Signal background tasks to yield the DB write lock."""
    global _write_lock_requested
    _write_lock_requested = True


def check_write_lock_requested() -> bool:
    """Check and clear the write lock request flag. Called by background tasks."""
    global _write_lock_requested
    if _write_lock_requested:
        _write_lock_requested = False
        return True
    return False


async def execute_write(func, *args, **kwargs):
    """Run a write function on its own connection.

    Signals background tasks to yield the write lock, then lets SQLite's
    busy_timeout handle contention.
    """
    request_write_lock()
    conn = await open_connection()
    try:
        return await func(conn, *args, **kwargs)
    finally:
        await conn.close()


async def open_connection() -> aiosqlite.Connection:
    """Open a standalone database connection (caller must close it)."""
    config = load_config()
    db_path = Path(config.get("database", "mail_hunter.db"))
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parent.parent / db_path
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA busy_timeout=30000")
    return conn
