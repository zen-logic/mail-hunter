import asyncio
import imaplib
import logging
import ssl
from datetime import datetime, timezone
from pathlib import Path

from mail_hunter.db import open_connection, check_write_lock_requested
from mail_hunter.config import decrypt_password
from mail_hunter.services.parser import parse_message
from mail_hunter.services.store import store_message
from mail_hunter.services.importer import (
    _ensure_folder,
    _is_duplicate,
    _insert_mail,
    _insert_tags,
)
from mail_hunter.services.dedup import recalculate_dup_counts
from mail_hunter.ws import broadcast

logger = logging.getLogger(__name__)

# --- File-based sync diagnostic logger ---
_sync_log_path = Path(__file__).resolve().parent.parent.parent / "sync_diag.log"
_sync_diag = logging.getLogger("sync_diag")
_sync_diag.setLevel(logging.DEBUG)
_sync_diag.propagate = False
_diag_handler = logging.FileHandler(_sync_log_path, mode="a")
_diag_handler.setFormatter(
    logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
)
_sync_diag.addHandler(_diag_handler)

BATCH_SIZE = 100
MAX_RECONNECTS = 3

# Gmail system folders to skip — labels on these messages are captured via
# X-GM-LABELS when syncing All Mail (or the user's preferred folder).
GMAIL_SKIP_FOLDERS = {
    "Sent Mail",
    "Important",
    "Starred",
    "Drafts",
    "Spam",
    "Bin",
    "Trash",
}

# Gmail folders that are truly label-backed — their messages appear in All Mail
# with X-GM-LABELS tags, so we query by tag instead of syncing the folder.
# Bin, Drafts, Spam, Starred are NOT here because their messages don't appear
# in All Mail with labels — they must be synced as real IMAP folders.
_GMAIL_FOLDER_TO_TAG = {
    "Sent Mail": "Sent",
    "Important": "Important",
}

# System label normalisation: raw backslash-prefixed -> clean name
_GMAIL_SYSTEM_LABELS = {
    "\\Inbox": "Inbox",
    "\\Sent": "Sent",
    "\\Important": "Important",
    "\\Starred": "Starred",
    "\\Draft": "Drafts",
    "\\Trash": "Trash",
    "\\Spam": "Spam",
}


def _normalise_label(label: str) -> str:
    """Normalise a Gmail label: strip backslash prefix from system labels."""
    result = _GMAIL_SYSTEM_LABELS.get(label)
    if result:
        return result
    # Handle double-escaped backslashes from quoted IMAP responses
    if label.startswith("\\\\"):
        return _GMAIL_SYSTEM_LABELS.get("\\" + label[2:], label[1:])
    return label


def _tokenize_labels(raw: str) -> list[str]:
    """Tokenize the content between X-GM-LABELS parentheses.

    Handles quoted strings (may contain spaces) and bare tokens.
    Example input: '\\Inbox "Legacy/2004" receipts'
    Returns: ['\\Inbox', 'Legacy/2004', 'receipts']
    """
    tokens = []
    i = 0
    while i < len(raw):
        if raw[i] == " ":
            i += 1
            continue
        if raw[i] == '"':
            # Quoted token — find matching close quote
            end = raw.find('"', i + 1)
            if end < 0:
                end = len(raw)
            tokens.append(raw[i + 1 : end])
            i = end + 1
        else:
            # Bare token — up to next space
            end = raw.find(" ", i)
            if end < 0:
                end = len(raw)
            tokens.append(raw[i:end])
            i = end
    return tokens


def _parse_gmail_labels(meta_line: bytes) -> list[str]:
    """Extract normalised labels from a FETCH response metadata line.

    Gmail returns something like:
        b'1 (X-GM-LABELS (\\Inbox "Legacy/2004" receipts) UID 123 RFC822 {456})'
    """
    text = meta_line.decode("utf-8", errors="replace")
    marker = "X-GM-LABELS ("
    start = text.find(marker)
    if start < 0:
        return []
    start += len(marker)
    # Find matching closing paren
    depth = 1
    pos = start
    while pos < len(text) and depth > 0:
        if text[pos] == "(":
            depth += 1
        elif text[pos] == ")":
            depth -= 1
        pos += 1
    inner = text[start : pos - 1]
    return [_normalise_label(t) for t in _tokenize_labels(inner)]


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


# --- In-memory sync coordination ---
# All slot/queue state is mutated only by synchronous functions.
# No await between reading state and writing state = no race.
_slot: int | None = None          # server_id currently syncing, or None
_queue: list[dict] = []           # in-memory ordered queue
_cancel_requested: set[int] = set()
_is_auto: bool = False            # True if the current _slot holder is auto-sync
_seq_counter: int = 0


def _next_seq() -> int:
    global _seq_counter
    _seq_counter += 1
    return _seq_counter


def enqueue(entry: dict) -> bool:
    """Add to queue. Sorts by (priority, seq). Updates existing entry if present."""
    for existing in _queue:
        if existing["server_id"] == entry["server_id"]:
            entry["seq"] = existing["seq"]  # keep original position
            _queue[:] = [
                e if e["server_id"] != entry["server_id"] else entry for e in _queue
            ]
            _queue.sort(key=lambda e: (e["priority"], e["seq"]))
            return True
    entry["seq"] = _next_seq()
    _queue.append(entry)
    _queue.sort(key=lambda e: (e["priority"], e["seq"]))
    return True


def dequeue(server_id: int) -> bool:
    """Remove from queue. Returns True if found."""
    before = len(_queue)
    _queue[:] = [e for e in _queue if e["server_id"] != server_id]
    return len(_queue) < before


def _claim_next() -> dict | None:
    """Pop front of queue and claim slot. SYNC — no await."""
    global _slot, _is_auto
    if _slot is not None or not _queue:
        return None
    entry = _queue.pop(0)
    _slot = entry["server_id"]
    _is_auto = entry["priority"] >= 10
    return entry


def release() -> None:
    """Release the slot. SYNC — no await."""
    global _slot, _is_auto
    _slot = None
    _is_auto = False


def cancel_sync(server_id: int):
    """Request cancellation of an active sync."""
    _sync_diag.info("cancel_sync(%d) — slot=%s", server_id, _slot)
    _cancel_requested.add(server_id)


def is_syncing(server_id: int) -> bool:
    return _slot == server_id


def is_auto_sync_active() -> bool:
    return _is_auto


def get_auto_sync_server_id() -> int | None:
    return _slot if _is_auto else None


async def start_next():
    """Try to start the next queued sync. Called after release() and after enqueue()."""
    while True:
        entry = _claim_next()  # synchronous — atomic
        if entry is None:
            return
        _sync_diag.info(
            "start_next: starting server=%d priority=%d",
            entry["server_id"], entry["priority"],
        )
        try:
            await _start_queued_sync(entry["server_id"], entry)
            return  # sync task created successfully
        except Exception:
            logger.exception(
                "Failed to start queued sync for server %d", entry["server_id"]
            )
            release()
            # loop to try the next queued entry


async def _persist_queue():
    """Write in-memory queue to DB. For crash recovery only — not read at runtime.

    Signals the running sync to yield the write lock so we can get through.
    The DELETE + INSERTs are in one transaction — if anything fails, the DB
    is unchanged.
    """
    from mail_hunter.db import request_write_lock

    request_write_lock()
    db = await open_connection()
    try:
        await db.execute("DELETE FROM sync_queue")
        for entry in _queue:
            await db.execute(
                "INSERT INTO sync_queue (server_id, folder, full, purge, priority) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    entry["server_id"],
                    entry.get("folder"),
                    entry.get("full", 0),
                    entry.get("purge", 0),
                    entry["priority"],
                ),
            )
        await db.commit()
    except Exception:
        logger.warning("_persist_queue: failed to persist queue to DB", exc_info=True)
    finally:
        await db.close()


def _connect(host: str, port: int, username: str, password: str, use_ssl: bool = True):
    """Connect and login to an IMAP server. Blocking — run in thread."""
    if use_ssl:
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
    else:
        conn = imaplib.IMAP4(host, port)
    conn.login(username, password)
    # Refresh capabilities after login — Gmail only advertises X-GM-EXT-1 post-auth
    typ, data = conn.capability()
    if typ == "OK" and data:
        conn.capabilities = tuple(data[0].upper().split())
    conn._is_gmail = b"X-GM-EXT-1" in (conn.capabilities or ())
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


def _fetch_message(conn, uid: int) -> tuple[bytes | None, list[str]]:
    """Fetch raw RFC822 message by UID. Returns (raw_bytes, gmail_labels). Blocking."""
    fetch_items = (
        "(RFC822 X-GM-LABELS)"
        if getattr(conn, "_is_gmail", False)
        else "(BODY.PEEK[])"
    )
    status, data = conn.uid("FETCH", str(uid), fetch_items)
    if status != "OK" or not data or data[0] is None:
        return None, []
    # data[0] is a tuple: (b'UID FLAGS ...', raw_bytes)
    if isinstance(data[0], tuple) and len(data[0]) >= 2:
        labels = (
            _parse_gmail_labels(data[0][0]) if getattr(conn, "_is_gmail", False) else []
        )
        return data[0][1], labels
    return None, []


async def test_connection(
    host: str, port: int, username: str, password: str, use_ssl: bool = True
) -> dict:
    """Test IMAP connection. Returns {ok, folders} or {ok, error}."""
    try:
        conn = await asyncio.to_thread(
            _connect, host, port, username, password, use_ssl
        )
        folders = await asyncio.to_thread(_list_folders, conn)
        is_gmail = getattr(conn, "_is_gmail", False)
        await asyncio.to_thread(conn.logout)
        return {"ok": True, "folders": folders, "is_gmail": is_gmail}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def sync_server(
    server_id: int, server: dict, *, full: bool = False, start_folder: str | None = None
):
    """Full incremental sync for a server. Runs as background task.

    Caller must have claimed the slot via _claim_next() before create_task().
    """
    _cancel_requested.discard(server_id)

    db = await open_connection()
    conn = None
    imported = 0
    skipped = 0
    errors = 0
    message_ids = []
    sync_cleared = False

    try:
        # Mark server as syncing so we can resume after restart
        await db.execute("UPDATE servers SET syncing = 1 WHERE id = ?", (server_id,))
        await db.commit()

        if full:
            await db.execute("DELETE FROM sync_state WHERE server_id = ?", (server_id,))
            await db.commit()

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

        # Persist Gmail capability flag
        is_gmail = getattr(conn, "_is_gmail", False)
        await db.execute(
            "UPDATE servers SET is_gmail = ? WHERE id = ?",
            (1 if is_gmail else 0, server_id),
        )
        await db.commit()

        # On Gmail, create virtual (label-backed) folders instead of skipping them
        virtual_folders = set()
        if is_gmail:
            for f in folders:
                for prefix in ("[Gmail]/", "[Google Mail]/"):
                    if f.startswith(prefix):
                        suffix = f[len(prefix) :]
                        tag = _GMAIL_FOLDER_TO_TAG.get(suffix)
                        if tag:
                            await _ensure_folder(db, server_id, f, label_tag=tag)
                            virtual_folders.add(f)
            if virtual_folders:
                logger.info("Gmail: %d virtual folders created", len(virtual_folders))

        # Single-folder sync: only sync the requested folder
        if start_folder and start_folder in folders:
            folders = [start_folder]

        # Create all non-virtual folders upfront so the tree updates immediately
        for folder_name in folders:
            if folder_name not in virtual_folders:
                await _ensure_folder(db, server_id, folder_name)
        await broadcast(
            {
                "type": "sync_folders",
                "server_id": server_id,
                "folders": folders,
            }
        )

        reconnects = 0
        folder_idx = 0
        while folder_idx < len(folders):
            folder_name = folders[folder_idx]

            if folder_name in virtual_folders:
                folder_idx += 1
                continue

            if server_id in _cancel_requested:
                break

            await broadcast(
                {
                    "type": "sync_progress",
                    "server_id": server_id,
                    "folder": folder_name,
                    "status": "scanning",
                }
            )

            try:
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
                    server["name"],
                    folder_name,
                    saved_last_uid,
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
                    await _save_sync_state(
                        db, server_id, folder_name, uid_validity, saved_last_uid
                    )
                    folder_idx += 1
                    continue

                logger.info(
                    "Sync %s/%s: %d new UIDs (from %d)",
                    server["name"],
                    folder_name,
                    len(new_uids),
                    new_uids[0],
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

                    # Yield BEFORE the IMAP fetch so the UI write lock
                    # isn't stuck behind a slow network operation
                    if check_write_lock_requested():
                        await db.commit()
                        await _save_sync_state(
                            db, server_id, folder_name, uid_validity, max_uid
                        )
                        await asyncio.sleep(0.2)

                    logger.info(
                        "Sync %s/%s [%d/%d]: fetching UID %d",
                        server["name"],
                        folder_name,
                        i + 1,
                        len(new_uids),
                        uid,
                    )
                    raw_bytes, gmail_labels = await asyncio.to_thread(
                        _fetch_message, conn, uid
                    )
                    if not raw_bytes:
                        logger.info("  UID %d: empty response, skipping", uid)
                        continue

                    parsed = parse_message(raw_bytes)

                    mail_data = None
                    existing_id = await _is_duplicate(db, server_id, parsed)
                    if existing_id:
                        skipped += 1
                        if gmail_labels:
                            await _insert_tags(db, existing_id, gmail_labels)
                        logger.info(
                            "  UID %d: duplicate (%d bytes), skipped",
                            uid,
                            len(raw_bytes),
                        )
                    else:
                        sha, path = store_message(raw_bytes)
                        mail_id = await _insert_mail(
                            db, server_id, folder_id, parsed, path, len(raw_bytes)
                        )
                        if gmail_labels:
                            await _insert_tags(db, mail_id, gmail_labels)
                        imported += 1
                        folder_count += 1
                        if parsed["message_id"]:
                            message_ids.append(parsed["message_id"])
                        logger.info(
                            "  UID %d: stored %d bytes, subject: %.60s",
                            uid,
                            len(raw_bytes),
                            parsed["subject"] or "(none)",
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

                    if (imported + skipped) % BATCH_SIZE == 0:
                        await db.commit()
                        await _save_sync_state(
                            db, server_id, folder_name, uid_validity, max_uid
                        )

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

                await db.commit()
                await _save_sync_state(
                    db, server_id, folder_name, uid_validity, max_uid
                )
                if check_write_lock_requested():
                    await asyncio.sleep(0.2)
                reconnects = 0  # successful folder resets counter
                folder_idx += 1

            except imaplib.IMAP4.abort as e:
                logger.warning(
                    "Sync %s/%s: connection lost (%s), reconnecting...",
                    server["name"],
                    folder_name,
                    e,
                )
                reconnects += 1
                if reconnects > MAX_RECONNECTS:
                    raise Exception(
                        f"Connection lost {reconnects} times, giving up"
                    ) from e
                # Save progress so far
                await db.commit()
                try:
                    await asyncio.to_thread(conn.logout)
                except Exception:
                    pass
                conn = await asyncio.to_thread(
                    _connect,
                    server["host"],
                    server["port"],
                    server["username"],
                    server["password"],
                    server.get("use_ssl", True),
                )
                logger.info("Reconnected, retrying folder %s", folder_name)
                # Don't increment folder_idx — retry the same folder from saved state

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
        else:
            await broadcast(
                {
                    "type": "sync_cancelled",
                    "server_id": server_id,
                    "imported": imported,
                }
            )

        # Success or user cancel — clear the flag
        sync_cleared = True

    except asyncio.CancelledError:
        logger.info(
            "Sync for server %d interrupted by shutdown, will resume on restart",
            server_id,
        )
        sync_cleared = False
    except Exception as e:
        logger.exception("Sync failed for server %d, will retry on restart", server_id)
        await broadcast(
            {
                "type": "sync_error",
                "server_id": server_id,
                "error": str(e),
            }
        )
        sync_cleared = False
    finally:
        if conn:
            try:
                await asyncio.wait_for(asyncio.to_thread(conn.logout), timeout=3)
            except Exception:
                pass
        if sync_cleared:
            try:
                await db.execute(
                    "UPDATE servers SET syncing = 0 WHERE id = ?", (server_id,)
                )
                await db.commit()
            except Exception:
                pass
        await db.close()
        _cancel_requested.discard(server_id)
        release()  # synchronous — slot is now free
        _sync_diag.info(
            "slot released server=%d — slot=%s queue=%d",
            server_id, _slot, len(_queue),
        )
        await start_next()  # immediately claims next (sync) then starts it (async)
        await _persist_queue()  # persist queue to DB for crash recovery


async def _start_queued_sync(server_id: int, queue_row: dict):
    """Load server creds and start a sync from queued params."""
    from pathlib import Path

    db = await open_connection()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, name, host, port, username, password, use_ssl, protocol "
            "FROM servers WHERE id = ?",
            (server_id,),
        )
        if not rows:
            raise ValueError(f"Server {server_id} not found — deleted while queued?")
        server = dict(rows[0])
        server["password"] = decrypt_password(server["password"])

        full = bool(queue_row["full"])
        purge = bool(queue_row["purge"])
        start_folder = queue_row["folder"]

        if purge:
            raw_rows = await db.execute_fetchall(
                "SELECT raw_path FROM mails WHERE server_id = ? AND raw_path IS NOT NULL",
                (server_id,),
            )
            await db.execute("DELETE FROM mails WHERE server_id = ?", (server_id,))
            await db.execute("DELETE FROM folders WHERE server_id = ?", (server_id,))
            await db.commit()
            for r in raw_rows:
                try:
                    Path(r["raw_path"]).unlink(missing_ok=True)
                except OSError:
                    pass
            full = True
    finally:
        await db.close()

    asyncio.create_task(
        sync_server(server_id, server, full=full, start_folder=start_folder)
    )
