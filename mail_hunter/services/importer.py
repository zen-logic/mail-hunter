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
    db: aiosqlite.Connection, server_id: int, folder_name: str, *, label_tag: str | None = None
) -> int:
    """Get or create a folder, return its id."""
    row = await db.execute_fetchall(
        "SELECT id FROM folders WHERE server_id = ? AND name = ?",
        (server_id, folder_name),
    )
    if row:
        if label_tag is not None:
            await db.execute(
                "UPDATE folders SET label_tag = ? WHERE id = ?",
                (label_tag, row[0]["id"]),
            )
        return row[0]["id"]
    cursor = await db.execute(
        "INSERT INTO folders (server_id, name, label_tag) VALUES (?, ?, ?)",
        (server_id, folder_name, label_tag),
    )
    await db.commit()
    return cursor.lastrowid


async def _is_duplicate(db: aiosqlite.Connection, server_id: int, parsed: dict):
    """Check if message already exists for this server (by message_id or content_hash).

    Returns the existing mail id (truthy) or None (falsy).
    """
    mid = parsed.get("message_id")
    if mid:
        rows = await db.execute_fetchall(
            "SELECT id FROM mails WHERE server_id = ? AND message_id = ? LIMIT 1",
            (server_id, mid),
        )
        if rows:
            return rows[0]["id"]

    chash = parsed.get("content_hash")
    if chash:
        rows = await db.execute_fetchall(
            "SELECT id FROM mails WHERE server_id = ? AND content_hash = ? LIMIT 1",
            (server_id, chash),
        )
        if rows:
            return rows[0]["id"]

    return None


async def _insert_mail(
    db: aiosqlite.Connection,
    server_id: int,
    folder_id: int | None,
    parsed: dict,
    raw_path: str,
    raw_size: int,
) -> int:
    """Insert a parsed message into the database. Returns mail id."""
    cursor = await db.execute(
        "INSERT INTO mails (server_id, folder_id, message_id, subject, from_name, from_addr, "
        "to_addr, cc_addr, reply_to, in_reply_to, references_header, date, size, "
        "body_text, attachment_count, content_hash, raw_path, raw_size) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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


async def _insert_tags(db: aiosqlite.Connection, mail_id: int, tags: list[str]):
    """Insert tags for a mail. Ignores duplicates."""
    for tag in tags:
        await db.execute(
            "INSERT OR IGNORE INTO tags (mail_id, tag) VALUES (?, ?)",
            (mail_id, tag),
        )


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


async def create_import_server(db: aiosqlite.Connection, name: str) -> int:
    """Create a server entry for an imported file (no auth details)."""
    cursor = await db.execute(
        "INSERT INTO servers (name, host, port, username, password, use_ssl, protocol) "
        "VALUES (?, '', 0, '', '', 0, 'import')",
        (name,),
    )
    await db.commit()
    return cursor.lastrowid


async def run_import(
    db: aiosqlite.Connection,
    raw_messages: list[bytes],
    server_id: int,
    filename: str = "file",
):
    """Import raw messages into a specific server. folder_id is NULL (flat)."""
    imported = 0
    skipped = 0
    message_ids = []
    last_mail_id = None
    total = len(raw_messages)

    await broadcast(
        {
            "type": "import_started",
            "filename": filename,
            "total": total,
            "server_id": server_id,
        }
    )

    for i, raw_bytes in enumerate(raw_messages):
        try:
            parsed = parse_message(raw_bytes)

            if await _is_duplicate(db, server_id, parsed):
                skipped += 1
                continue

            sha, path = store_message(raw_bytes)
            last_mail_id = await _insert_mail(
                db, server_id, None, parsed, path, len(raw_bytes)
            )
            imported += 1
            if parsed["message_id"]:
                message_ids.append(parsed["message_id"])

            if check_write_lock_requested():
                await db.commit()
                await asyncio.sleep(0.2)
            elif imported % BATCH_SIZE == 0:
                await db.commit()

            await broadcast(
                {
                    "type": "import_progress",
                    "server_id": server_id,
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
            "total": total,
            "server_id": server_id,
            "mail_id": last_mail_id if imported == 1 else None,
        }
    )
