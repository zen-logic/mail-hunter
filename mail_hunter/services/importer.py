import asyncio
import logging
import mailbox
from pathlib import Path

import aiosqlite

from mail_hunter.db import check_write_lock_requested
from mail_hunter.services.parser import parse_message
from mail_hunter.services.store import store_message
from mail_hunter.services.dedup import recalculate_dup_counts
from mail_hunter.ws import broadcast

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


async def _ensure_folder(
    db: aiosqlite.Connection, server_id: int, folder_name: str
) -> int:
    """Get or create a folder, return its id."""
    row = await db.execute_fetchall(
        "SELECT id FROM folders WHERE server_id = ? AND name = ?",
        (server_id, folder_name),
    )
    if row:
        return row[0]["id"]
    cursor = await db.execute(
        "INSERT INTO folders (server_id, name) VALUES (?, ?)",
        (server_id, folder_name),
    )
    await db.commit()
    return cursor.lastrowid


async def _is_duplicate(db: aiosqlite.Connection, server_id: int, parsed: dict) -> bool:
    """Check if message already exists for this server (by message_id or content_hash)."""
    mid = parsed.get("message_id")
    if mid:
        rows = await db.execute_fetchall(
            "SELECT id FROM mails WHERE server_id = ? AND message_id = ? LIMIT 1",
            (server_id, mid),
        )
        if rows:
            return True

    chash = parsed.get("content_hash")
    if chash:
        rows = await db.execute_fetchall(
            "SELECT id FROM mails WHERE server_id = ? AND content_hash = ? LIMIT 1",
            (server_id, chash),
        )
        if rows:
            return True

    return False


async def _insert_mail(
    db: aiosqlite.Connection,
    server_id: int,
    folder_id: int,
    parsed: dict,
    raw_path: str,
    raw_size: int,
) -> int:
    """Insert a parsed message into the database. Returns mail id."""
    cursor = await db.execute(
        "INSERT INTO mails (server_id, folder_id, message_id, subject, from_name, from_addr, "
        "to_addr, cc_addr, reply_to, in_reply_to, references_header, date, size, "
        "body_preview, body_text, attachment_count, content_hash, raw_path, raw_size) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            server_id,
            folder_id,
            parsed["message_id"],
            parsed["subject"],
            parsed["from_name"],
            parsed["from_addr"],
            parsed["to_addr"],
            parsed["cc_addr"],
            parsed["reply_to"],
            parsed["in_reply_to"],
            parsed["references_header"],
            parsed["date"],
            parsed["size"],
            parsed["body_preview"],
            parsed["body_text"],
            parsed["attachment_count"],
            parsed["content_hash"],
            raw_path,
            raw_size,
        ),
    )
    mail_id = cursor.lastrowid

    for att in parsed.get("attachments", []):
        await db.execute(
            "INSERT INTO attachments (mail_id, filename, content_type, size) VALUES (?, ?, ?, ?)",
            (mail_id, att["filename"], att["content_type"], att["size"]),
        )

    return mail_id


def _is_mbox_file(file_path: str) -> bool:
    """Detect mbox format by checking if file starts with 'From '."""
    try:
        with open(file_path, "rb") as f:
            return f.read(5) == b"From "
    except OSError:
        return False


def scan_raw_messages(file_paths: list[str], is_mbox: bool = False) -> list[bytes]:
    """Read raw message bytes from EML files or an MBOX file. Synchronous."""
    raw_messages = []
    for fp in file_paths:
        try:
            if is_mbox or _is_mbox_file(fp):
                mbox = mailbox.mbox(fp)
                for msg in mbox:
                    raw_messages.append(msg.as_bytes())
                mbox.close()
            else:
                raw_messages.append(Path(fp).read_bytes())
        except OSError:
            logger.exception("Failed to read %s", fp)
    return raw_messages


async def build_server_cache(db: aiosqlite.Connection) -> dict[str, int]:
    """Build a mapping of lowercase email address -> server_id from all servers."""
    rows = await db.execute_fetchall("SELECT id, username FROM servers")
    cache = {}
    for r in rows:
        username = (r["username"] or "").strip().lower()
        if username:
            cache[username] = r["id"]
    return cache


def find_server_for_message(parsed: dict, server_cache: dict[str, int]) -> int | None:
    """Find a matching server for a parsed message by checking from/to addresses."""
    from_addr = (parsed.get("from_addr") or "").strip().lower()
    to_addr = (parsed.get("to_addr") or "").strip().lower()

    # Check from_addr
    if from_addr in server_cache:
        return server_cache[from_addr]

    # Check to_addr (may contain multiple comma-separated addresses)
    for addr in to_addr.split(","):
        addr = addr.strip()
        # Strip angle brackets and name portion if present
        if "<" in addr and ">" in addr:
            addr = addr[addr.index("<") + 1 : addr.index(">")].strip()
        if addr in server_cache:
            return server_cache[addr]

    # Check from_addr with angle brackets too
    if "<" in from_addr and ">" in from_addr:
        bare = from_addr[from_addr.index("<") + 1 : from_addr.index(">")].strip()
        if bare in server_cache:
            return server_cache[bare]

    return None


def collect_unmatched_addresses(
    messages: list[dict], server_cache: dict[str, int]
) -> dict[str, int]:
    """For messages with no matching server, collect all unique addresses with counts."""
    addr_counts: dict[str, int] = {}
    for parsed in messages:
        if find_server_for_message(parsed, server_cache) is not None:
            continue
        # Collect from and to addresses
        from_addr = (parsed.get("from_addr") or "").strip().lower()
        to_addr = (parsed.get("to_addr") or "").strip().lower()
        for raw in [from_addr] + to_addr.split(","):
            addr = raw.strip()
            if "<" in addr and ">" in addr:
                addr = addr[addr.index("<") + 1 : addr.index(">")].strip()
            if addr and "@" in addr:
                addr_counts[addr] = addr_counts.get(addr, 0) + 1
    return addr_counts


async def create_import_server(db: aiosqlite.Connection, address: str) -> int:
    """Create a server entry for an import-only address (no auth details)."""
    cursor = await db.execute(
        "INSERT INTO servers (name, host, port, username, password, use_ssl, protocol) "
        "VALUES (?, '', 0, ?, '', 0, 'import')",
        (address, address),
    )
    await db.commit()
    return cursor.lastrowid


# folder_cache: server_id -> folder_id
_folder_cache: dict[int, int] = {}


async def run_import(
    db: aiosqlite.Connection,
    raw_messages: list[bytes],
    filename: str = "file",
):
    """Import raw messages, auto-routing each to the correct server by address."""
    global _folder_cache
    _folder_cache = {}

    server_cache = await build_server_cache(db)

    imported = 0
    skipped = 0
    unroutable = 0
    message_ids = []
    server_ids_seen: set[int] = set()
    last_mail_id = None
    total = len(raw_messages)

    await broadcast({"type": "import_started", "filename": filename, "total": total})

    for i, raw_bytes in enumerate(raw_messages):
        try:
            parsed = parse_message(raw_bytes)
            server_id = find_server_for_message(parsed, server_cache)

            if server_id is None:
                unroutable += 1
                continue

            # Get or create Import folder for this server
            if server_id not in _folder_cache:
                _folder_cache[server_id] = await _ensure_folder(db, server_id, "Import")
            folder_id = _folder_cache[server_id]

            if await _is_duplicate(db, server_id, parsed):
                skipped += 1
                continue

            sha, path = store_message(raw_bytes)
            last_mail_id = await _insert_mail(
                db, server_id, folder_id, parsed, path, len(raw_bytes)
            )
            server_ids_seen.add(server_id)
            imported += 1
            if parsed["message_id"]:
                message_ids.append(parsed["message_id"])

            if check_write_lock_requested():
                await db.commit()
                await asyncio.sleep(0.2)
            elif imported % BATCH_SIZE == 0:
                await db.commit()

            if imported % BATCH_SIZE == 0:
                await broadcast(
                    {
                        "type": "import_progress",
                        "count": imported,
                        "total": total,
                        "skipped": skipped,
                    }
                )
        except Exception:
            logger.exception("Failed to import message %d", i)

    await db.commit()

    if message_ids:
        await recalculate_dup_counts(db, message_ids)

    await broadcast(
        {
            "type": "import_completed",
            "count": imported,
            "skipped": skipped,
            "unroutable": unroutable,
            "total": total,
            "server_id": next(iter(server_ids_seen)) if len(server_ids_seen) == 1 else None,
            "mail_id": last_mail_id if imported == 1 else None,
        }
    )
