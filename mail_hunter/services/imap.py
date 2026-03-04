import asyncio
import imaplib
import logging
import ssl
from datetime import datetime, timezone

from mail_hunter.db import open_connection, check_write_lock_requested
from mail_hunter.services.parser import parse_message
from mail_hunter.services.store import store_message
from mail_hunter.services.importer import _ensure_folder, _is_duplicate, _insert_mail
from mail_hunter.services.dedup import recalculate_dup_counts
from mail_hunter.ws import broadcast

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


async def _save_sync_state(db, server_id, folder_name, uid_validity, last_uid):
    """Persist sync checkpoint so progress survives interruptions."""
    await db.execute(
        "INSERT INTO sync_state (server_id, folder_name, uid_validity, last_uid, last_sync) "
        "VALUES (?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(server_id, folder_name) DO UPDATE SET "
        "uid_validity=excluded.uid_validity, last_uid=excluded.last_uid, last_sync=datetime('now')",
        (server_id, folder_name, uid_validity, last_uid),
    )
    await db.commit()


# Active sync tasks: server_id -> True (used for cancellation)
_cancel_requested: set[int] = set()
_active_syncs: set[int] = set()


def _connect(host: str, port: int, username: str, password: str, use_ssl: bool = True):
    """Connect and login to an IMAP server. Blocking — run in thread."""
    if use_ssl:
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
    else:
        conn = imaplib.IMAP4(host, port)
    conn.login(username, password)
    return conn


def _list_folders(conn) -> list[str]:
    """List all folders on the IMAP server. Blocking."""
    status, data = conn.list()
    if status != "OK":
        return []
    folders = []
    for item in data:
        if isinstance(item, bytes):
            # Parse IMAP LIST response: (\flags) "delimiter" name
            # Name may be quoted ("INBOX") or unquoted (INBOX)
            decoded = item.decode("utf-8", errors="replace")
            # Skip non-selectable folders
            if "\\Noselect" in decoded:
                continue
            # Find the delimiter (quoted char after the flags parenthesis)
            # Format: (\flags) "." "FolderName"  or  (\flags) "." FolderName
            paren_end = decoded.find(")")
            if paren_end < 0:
                continue
            rest = decoded[paren_end + 1 :].strip()
            # rest is now: "." "FolderName"  or  "." FolderName
            # Skip the quoted delimiter
            if rest.startswith('"'):
                delim_end = rest.find('"', 1)
                if delim_end < 0:
                    continue
                rest = rest[delim_end + 1 :].strip()
            else:
                # NIL delimiter or unexpected format
                rest = rest.split(None, 1)[-1] if " " in rest else rest
            # rest is now: "FolderName" or FolderName
            if rest.startswith('"') and rest.endswith('"'):
                name = rest[1:-1]
            else:
                name = rest
            if name:
                folders.append(name)
    return folders


def _fetch_uids_since(conn, folder: str, last_uid: int) -> tuple[int | None, list[int]]:
    """Select folder, return (uid_validity, list_of_new_uids). Blocking."""
    status, data = conn.select(f'"{folder}"', readonly=True)
    if status != "OK":
        return None, []

    # Get UIDVALIDITY
    status, data = conn.status(f'"{folder}"', "(UIDVALIDITY)")
    uid_validity = None
    if status == "OK" and data[0]:
        text = data[0].decode("utf-8", errors="replace")
        # Response: "INBOX" (UIDVALIDITY 12345)
        if "UIDVALIDITY" in text:
            val = text.split("UIDVALIDITY")[1].strip().rstrip(")")
            try:
                uid_validity = int(val)
            except ValueError:
                pass

    # Search for UIDs greater than last_uid
    if last_uid > 0:
        search_criteria = f"UID {last_uid + 1}:*"
    else:
        search_criteria = "ALL"

    status, data = conn.uid("SEARCH", None, search_criteria)
    if status != "OK" or not data[0]:
        return uid_validity, []

    uids = []
    for uid_str in data[0].split():
        uid = int(uid_str)
        if uid > last_uid:
            uids.append(uid)
    return uid_validity, sorted(uids)


def _fetch_message(conn, uid: int) -> bytes | None:
    """Fetch raw RFC822 message by UID. Blocking."""
    status, data = conn.uid("FETCH", str(uid), "(RFC822)")
    if status != "OK" or not data or data[0] is None:
        return None
    # data[0] is a tuple: (b'UID FLAGS ...', raw_bytes)
    if isinstance(data[0], tuple) and len(data[0]) >= 2:
        return data[0][1]
    return None


async def test_connection(
    host: str, port: int, username: str, password: str, use_ssl: bool = True
) -> dict:
    """Test IMAP connection. Returns {ok, folders} or {ok, error}."""
    try:
        conn = await asyncio.to_thread(
            _connect, host, port, username, password, use_ssl
        )
        folders = await asyncio.to_thread(_list_folders, conn)
        await asyncio.to_thread(conn.logout)
        return {"ok": True, "folders": folders}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def sync_server(server_id: int, server: dict, *, full: bool = False, start_folder: str | None = None):
    """Full incremental sync for a server. Runs as background task."""
    if server_id in _active_syncs:
        await broadcast(
            {
                "type": "sync_error",
                "server_id": server_id,
                "error": "Sync already in progress",
            }
        )
        return

    _active_syncs.add(server_id)
    _cancel_requested.discard(server_id)

    db = await open_connection()

    # Mark server as syncing so we can resume after restart
    await db.execute(
        "UPDATE servers SET syncing = 1 WHERE id = ?", (server_id,)
    )
    await db.commit()

    if full:
        await db.execute(
            "DELETE FROM sync_state WHERE server_id = ?", (server_id,)
        )
        await db.commit()
    conn = None
    imported = 0
    skipped = 0
    errors = 0
    message_ids = []

    try:
        await broadcast(
            {
                "type": "sync_started",
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

        folders = await asyncio.to_thread(_list_folders, conn)
        if not folders:
            await broadcast(
                {
                    "type": "sync_error",
                    "server_id": server_id,
                    "error": "No folders found",
                }
            )
            return

        # Reorder so start_folder is synced first
        if start_folder and start_folder in folders:
            folders.remove(start_folder)
            folders.insert(0, start_folder)

        # Create all folders upfront so the tree updates immediately
        for folder_name in folders:
            await _ensure_folder(db, server_id, folder_name)
        await broadcast(
            {
                "type": "sync_folders",
                "server_id": server_id,
                "folders": folders,
            }
        )

        for folder_name in folders:
            if server_id in _cancel_requested:
                _active_syncs.discard(server_id)
                await broadcast(
                    {
                        "type": "sync_cancelled",
                        "server_id": server_id,
                        "imported": imported,
                    }
                )
                break

            await broadcast(
                {
                    "type": "sync_progress",
                    "server_id": server_id,
                    "folder": folder_name,
                    "status": "scanning",
                }
            )

            # Get sync state for this folder
            row = await db.execute_fetchall(
                "SELECT uid_validity, last_uid FROM sync_state "
                "WHERE server_id = ? AND folder_name = ?",
                (server_id, folder_name),
            )
            saved_uid_validity = row[0]["uid_validity"] if row else None
            saved_last_uid = row[0]["last_uid"] if row else 0

            logger.info(
                "Sync %s/%s: last_uid=%d",
                server["name"], folder_name, saved_last_uid,
            )

            uid_validity, new_uids = await asyncio.to_thread(
                _fetch_uids_since, conn, folder_name, saved_last_uid
            )

            # UID validity changed — reset and re-fetch all
            if (
                uid_validity is not None
                and saved_uid_validity is not None
                and uid_validity != saved_uid_validity
            ):
                logger.warning(
                    "UIDVALIDITY changed for %s/%s: %s -> %s, re-syncing folder",
                    server["name"],
                    folder_name,
                    saved_uid_validity,
                    uid_validity,
                )
                saved_last_uid = 0
                uid_validity, new_uids = await asyncio.to_thread(
                    _fetch_uids_since, conn, folder_name, 0
                )

            if not new_uids:
                logger.info("Sync %s/%s: up to date", server["name"], folder_name)
                await _save_sync_state(db, server_id, folder_name, uid_validity, saved_last_uid)
                continue

            logger.info(
                "Sync %s/%s: %d new UIDs (from %d)",
                server["name"], folder_name, len(new_uids), new_uids[0],
            )

            folder_id = await _ensure_folder(db, server_id, folder_name)
            max_uid = saved_last_uid

            count_row = await db.execute_fetchall(
                "SELECT COUNT(*) as cnt FROM mails WHERE folder_id = ?",
                (folder_id,),
            )
            existing_count = count_row[0]["cnt"] if count_row else 0
            folder_count = existing_count

            await broadcast(
                {
                    "type": "sync_progress",
                    "server_id": server_id,
                    "folder": folder_name,
                    "count": 0,
                    "total": len(new_uids),
                    "existing_count": existing_count,
                }
            )

            for i, uid in enumerate(new_uids):
                if server_id in _cancel_requested:
                    break

                try:
                    logger.info(
                        "Sync %s/%s [%d/%d]: fetching UID %d",
                        server["name"], folder_name, i + 1, len(new_uids), uid,
                    )
                    raw_bytes = await asyncio.to_thread(_fetch_message, conn, uid)
                    if not raw_bytes:
                        logger.info("  UID %d: empty response, skipping", uid)
                        continue

                    parsed = parse_message(raw_bytes)

                    mail_data = None
                    if await _is_duplicate(db, server_id, parsed):
                        skipped += 1
                        logger.info(
                            "  UID %d: duplicate (%d bytes), skipped",
                            uid, len(raw_bytes),
                        )
                    else:
                        sha, path = store_message(raw_bytes)
                        mail_id = await _insert_mail(
                            db, server_id, folder_id, parsed, path, len(raw_bytes)
                        )
                        imported += 1
                        folder_count += 1
                        if parsed["message_id"]:
                            message_ids.append(parsed["message_id"])
                        logger.info(
                            "  UID %d: stored %d bytes, subject: %.60s",
                            uid, len(raw_bytes), parsed["subject"] or "(none)",
                        )
                        mail_data = {
                            "id": mail_id,
                            "subject": parsed["subject"],
                            "from_name": parsed["from_name"],
                            "from_addr": parsed["from_addr"],
                            "date": parsed["date"],
                            "size": parsed["size"],
                            "unread": 0,
                            "attachment_count": parsed["attachment_count"],
                        }

                    if uid > max_uid:
                        max_uid = uid

                    if check_write_lock_requested():
                        await db.commit()
                        await _save_sync_state(db, server_id, folder_name, uid_validity, max_uid)
                        await asyncio.sleep(0.2)
                    elif (imported + skipped) % BATCH_SIZE == 0:
                        await db.commit()
                        await _save_sync_state(db, server_id, folder_name, uid_validity, max_uid)

                    msg_out = {
                        "type": "sync_progress",
                        "server_id": server_id,
                        "folder": folder_name,
                        "count": i + 1,
                        "total": len(new_uids),
                        "existing_count": existing_count,
                        "folder_count": folder_count,
                    }
                    if mail_data:
                        msg_out["mail"] = mail_data
                    await broadcast(msg_out)
                except Exception:
                    logger.exception(
                        "Failed to fetch/process UID %d in %s", uid, folder_name
                    )
                    errors += 1

            await db.commit()
            await _save_sync_state(db, server_id, folder_name, uid_validity, max_uid)
            if check_write_lock_requested():
                await asyncio.sleep(0.2)

        # Recalculate dup counts
        if message_ids:
            await recalculate_dup_counts(db, message_ids)

        # Update server last_sync
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE servers SET last_sync = ? WHERE id = ?", (now, server_id)
        )
        await db.commit()

        if server_id not in _cancel_requested:
            await broadcast(
                {
                    "type": "sync_completed",
                    "server_id": server_id,
                    "imported": imported,
                    "skipped": skipped,
                    "errors": errors,
                }
            )

    except asyncio.CancelledError:
        logger.info("Sync for server %d interrupted by shutdown, will resume on restart", server_id)
        if conn:
            try:
                await asyncio.to_thread(conn.logout)
            except Exception:
                pass
        await db.close()
        _active_syncs.discard(server_id)
        return
    except Exception as e:
        logger.exception("Sync failed for server %d", server_id)
        await broadcast(
            {
                "type": "sync_error",
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
        # Clear syncing flag — only reached on completion, cancel, or error (not shutdown)
        try:
            await db.execute(
                "UPDATE servers SET syncing = 0 WHERE id = ?", (server_id,)
            )
            await db.commit()
        except Exception:
            pass
        await db.close()
        _active_syncs.discard(server_id)
        _cancel_requested.discard(server_id)


def cancel_sync(server_id: int):
    """Request cancellation of an active sync."""
    _cancel_requested.add(server_id)


def is_syncing(server_id: int) -> bool:
    return server_id in _active_syncs
