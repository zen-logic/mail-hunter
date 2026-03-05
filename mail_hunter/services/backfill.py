import asyncio
import logging
import re

from mail_hunter.db import open_connection, check_write_lock_requested
from mail_hunter.services.imap import _connect, _parse_gmail_labels
from mail_hunter.services.importer import _insert_tags
from mail_hunter.ws import broadcast

logger = logging.getLogger(__name__)

BATCH_SIZE = 200

_active_backfills: set[int] = set()
_cancel_backfill_requested: set[int] = set()

# Regex to extract Message-ID from a header fragment
_MESSAGE_ID_RE = re.compile(rb"Message-ID:\s*(<[^>]+>)", re.IGNORECASE)


def is_backfilling(server_id: int) -> bool:
    return server_id in _active_backfills


def cancel_backfill(server_id: int):
    """Request cancellation of an active backfill."""
    _cancel_backfill_requested.add(server_id)


def _find_all_mail_folder(conn) -> str | None:
    """Find the All Mail folder by looking for the \\All attribute flag."""
    status, data = conn.list()
    if status != "OK":
        return None
    for item in data:
        if not isinstance(item, bytes):
            continue
        decoded = item.decode("utf-8", errors="replace")
        # Look for \All attribute in the flags section
        paren_end = decoded.find(")")
        if paren_end < 0:
            continue
        flags = decoded[: paren_end + 1]
        if "\\All" not in flags:
            continue
        # Extract folder name from rest of line
        rest = decoded[paren_end + 1 :].strip()
        if rest.startswith('"'):
            delim_end = rest.find('"', 1)
            if delim_end < 0:
                continue
            rest = rest[delim_end + 1 :].strip()
        else:
            rest = rest.split(None, 1)[-1] if " " in rest else rest
        if rest.startswith('"') and rest.endswith('"'):
            return rest[1:-1]
        return rest
    return None


def _fetch_labels_batch(conn, uids: list[int]) -> list[tuple[str | None, list[str]]]:
    """Fetch X-GM-LABELS + Message-ID header for a batch of UIDs. Blocking.

    Returns list of (message_id, labels) tuples.
    """
    uid_set = ",".join(str(u) for u in uids)
    status, data = conn.uid(
        "FETCH",
        uid_set,
        "(X-GM-LABELS BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])",
    )
    if status != "OK" or not data:
        return []

    results = []
    i = 0
    while i < len(data):
        item = data[i]
        if item is None or item == b")":
            i += 1
            continue
        if isinstance(item, tuple) and len(item) >= 2:
            meta_bytes = item[0]
            header_bytes = item[1]
            labels = _parse_gmail_labels(meta_bytes)
            # Extract Message-ID from header
            message_id = None
            match = _MESSAGE_ID_RE.search(header_bytes)
            if match:
                message_id = match.group(1).decode("utf-8", errors="replace")
            results.append((message_id, labels))
        i += 1

    return results


async def backfill_labels(server_id: int, server: dict):
    """Background task: backfill Gmail labels for existing messages."""
    if server_id in _active_backfills:
        await broadcast(
            {
                "type": "backfill_error",
                "server_id": server_id,
                "error": "Backfill already in progress",
            }
        )
        return

    _active_backfills.add(server_id)
    _cancel_backfill_requested.discard(server_id)

    db = await open_connection()
    conn = None
    tagged = 0

    try:
        await broadcast(
            {
                "type": "backfill_started",
                "server_id": server_id,
                "server_name": server["name"],
            }
        )

        conn = await asyncio.to_thread(
            _connect,
            server["host"],
            server["port"],
            server["username"],
            server["password"],
            server.get("use_ssl", True),
        )

        if not getattr(conn, "_is_gmail", False):
            await broadcast(
                {
                    "type": "backfill_error",
                    "server_id": server_id,
                    "error": "Not a Gmail server",
                }
            )
            return

        all_mail = await asyncio.to_thread(_find_all_mail_folder, conn)
        if not all_mail:
            await broadcast(
                {
                    "type": "backfill_error",
                    "server_id": server_id,
                    "error": "Could not find All Mail folder",
                }
            )
            return

        logger.info("Backfill: selecting %s", all_mail)
        status, _data = await asyncio.to_thread(
            conn.select,
            f'"{all_mail}"',
            True,  # readonly
        )
        if status != "OK":
            await broadcast(
                {
                    "type": "backfill_error",
                    "server_id": server_id,
                    "error": f"Could not select {all_mail}",
                }
            )
            return

        # Get all UIDs
        status, data = await asyncio.to_thread(conn.uid, "SEARCH", None, "ALL")
        if status != "OK" or not data[0]:
            await broadcast(
                {
                    "type": "backfill_completed",
                    "server_id": server_id,
                    "tagged": 0,
                }
            )
            return

        all_uids = [int(u) for u in data[0].split()]
        total = len(all_uids)
        logger.info("Backfill: %d UIDs in %s", total, all_mail)

        # Pre-load message_id -> mail_id mapping for this server
        rows = await db.execute_fetchall(
            "SELECT id, message_id FROM mails WHERE server_id = ? AND message_id IS NOT NULL",
            (server_id,),
        )
        mid_to_id: dict[str, int] = {r["message_id"]: r["id"] for r in rows}
        logger.info(
            "Backfill: %d messages in DB for server %d", len(mid_to_id), server_id
        )

        count = 0
        for batch_start in range(0, total, BATCH_SIZE):
            if server_id in _cancel_backfill_requested:
                await broadcast(
                    {
                        "type": "backfill_cancelled",
                        "server_id": server_id,
                        "tagged": tagged,
                    }
                )
                return

            batch_uids = all_uids[batch_start : batch_start + BATCH_SIZE]
            results = await asyncio.to_thread(_fetch_labels_batch, conn, batch_uids)

            for message_id, labels in results:
                if not message_id or not labels:
                    continue
                mail_id = mid_to_id.get(message_id)
                if mail_id is None:
                    continue
                await _insert_tags(db, mail_id, labels)
                tagged += 1

            count += len(batch_uids)
            await db.commit()

            if check_write_lock_requested():
                await asyncio.sleep(0.2)

            await broadcast(
                {
                    "type": "backfill_progress",
                    "server_id": server_id,
                    "count": count,
                    "total": total,
                    "tagged": tagged,
                }
            )

        await broadcast(
            {
                "type": "backfill_completed",
                "server_id": server_id,
                "tagged": tagged,
            }
        )

    except asyncio.CancelledError:
        logger.info("Backfill for server %d interrupted by shutdown", server_id)
    except Exception as e:
        logger.exception("Backfill failed for server %d", server_id)
        await broadcast(
            {
                "type": "backfill_error",
                "server_id": server_id,
                "error": str(e),
            }
        )
    finally:
        if conn:
            try:
                await asyncio.to_thread(conn.logout)
            except Exception:
                pass
        await db.close()
        _active_backfills.discard(server_id)
        _cancel_backfill_requested.discard(server_id)
