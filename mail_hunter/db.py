import aiosqlite
from pathlib import Path
from mail_hunter.config import load_config

_db = None

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
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


async def init_db(db: aiosqlite.Connection):
    for statement in SCHEMA.split(";"):
        stmt = statement.strip()
        if stmt:
            await db.execute(stmt)
    await db.commit()


async def close_db():
    global _db
    if _db is not None:
        await _db.close()
        _db = None


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
