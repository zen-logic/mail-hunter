import asyncio
import base64
import email
import email.policy
import io
import logging
import re
import zipfile
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from mail_hunter import __version__
from mail_hunter.config import encrypt_password
from mail_hunter.db import get_db, open_connection, request_write_lock
from mail_hunter.services.imap import is_syncing
from mail_hunter.services.parser import _extract_body, _extract_body_html
from mail_hunter.services.store import (
    extract_attachment_from_path,
    read_raw,
    _get_archive_root,
)
from mail_hunter.ws import broadcast

logger = logging.getLogger(__name__)

SORT_COLUMNS = {
    "from": "COALESCE(NULLIF(from_name,''),from_addr)",
    "to": "to_addr",
    "subject": "subject",
    "date": "date",
    "size": "size",
}
# For queries using table alias "m."
SORT_COLUMNS_M = {
    "from": "COALESCE(NULLIF(m.from_name,''),m.from_addr)",
    "to": "m.to_addr",
    "subject": "m.subject",
    "date": "m.date",
    "size": "m.size",
}
PAGE_SIZE = 100


def _sort_params(request):
    """Extract validated sort/page params from query string."""
    sort_key = request.query_params.get("sort", "date")
    sort_col = SORT_COLUMNS.get(sort_key, "date")
    sort_col_m = SORT_COLUMNS_M.get(sort_key, "m.date")
    sort_dir = "ASC" if request.query_params.get("sortDir") == "asc" else "DESC"
    try:
        page = max(0, int(request.query_params.get("page", 0)))
    except (ValueError, TypeError):
        page = 0
    return sort_col, sort_col_m, sort_dir, page


async def list_servers(request: Request):
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, name, host, port, username, last_sync, is_gmail, sync_enabled, sync_interval FROM servers ORDER BY name COLLATE NOCASE"
    )
    # Fetch all folders with counts in one query (avoids N+1)
    all_folders = await db.execute_fetchall(
        "SELECT f.id, f.server_id, f.name, f.label_tag, "
        "CASE WHEN f.label_tag IS NOT NULL THEN "
        "  (SELECT COUNT(*) FROM tags t JOIN mails m ON t.mail_id = m.id "
        "   WHERE t.tag = f.label_tag AND m.server_id = f.server_id) "
        "ELSE "
        "  (SELECT COUNT(*) FROM mails m WHERE m.folder_id = f.id) "
        "END as count "
        "FROM folders f ORDER BY f.name"
    )
    folders_by_server = {}
    for f in all_folders:
        folders_by_server.setdefault(f["server_id"], []).append(
            {"name": f["name"], "count": f["count"]}
        )
    servers = []
    for r in rows:
        servers.append(
            {
                "id": r["id"],
                "name": r["name"],
                "host": r["host"],
                "port": r["port"],
                "username": r["username"],
                "last_sync": r["last_sync"],
                "is_gmail": bool(r["is_gmail"]),
                "sync_enabled": bool(r["sync_enabled"]),
                "sync_interval": r["sync_interval"],
                "syncing": is_syncing(r["id"]),
                "folders": folders_by_server.get(r["id"], []),
            }
        )
    return JSONResponse(servers)


async def create_server(request: Request):
    data = await request.json()
    name = data.get("name", "").strip()
    host = data.get("host", "").strip()
    port = data.get("port", 993)
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not host or not username:
        return JSONResponse({"error": "host and username required"}, status_code=400)

    request_write_lock()
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO servers (name, host, port, username, password) VALUES (?, ?, ?, ?, ?)",
        (name or host, host, port, username, encrypt_password(password)),
    )
    await db.commit()
    return JSONResponse({"id": cursor.lastrowid}, status_code=201)


async def update_server(request: Request):
    server_id = request.path_params["server_id"]
    data = await request.json()
    name = data.get("name", "").strip()
    host = data.get("host", "").strip()
    port = data.get("port", 993)
    username = data.get("username", "").strip()
    password = data.get("password", "")

    request_write_lock()
    db = await get_db()

    # Check if this is an import-only server
    row = await db.execute_fetchall(
        "SELECT protocol FROM servers WHERE id = ?", (server_id,)
    )
    if not row:
        return JSONResponse({"error": "server not found"}, status_code=404)

    sync_enabled = 1 if data.get("sync_enabled", True) else 0
    try:
        sync_interval = max(0, int(data.get("sync_interval", 15)))
    except (ValueError, TypeError):
        sync_interval = 15

    if row[0]["protocol"] == "import":
        # Import servers: only the name can be updated
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        await db.execute(
            "UPDATE servers SET name=? WHERE id=?",
            (name, server_id),
        )
    else:
        if not host or not username:
            return JSONResponse(
                {"error": "host and username required"}, status_code=400
            )
        if password:
            await db.execute(
                "UPDATE servers SET name=?, host=?, port=?, username=?, password=?, sync_enabled=?, sync_interval=? WHERE id=?",
                (
                    name or host,
                    host,
                    port,
                    username,
                    encrypt_password(password),
                    sync_enabled,
                    sync_interval,
                    server_id,
                ),
            )
        else:
            await db.execute(
                "UPDATE servers SET name=?, host=?, port=?, username=?, sync_enabled=?, sync_interval=? WHERE id=?",
                (name or host, host, port, username, sync_enabled, sync_interval, server_id),
            )
    await db.commit()
    return JSONResponse({"ok": True})


async def delete_server(request: Request):
    server_id = request.path_params["server_id"]
    db = await get_db()

    # Fetch server info before spawning background task
    row = await db.execute_fetchall(
        "SELECT name FROM servers WHERE id = ?", (server_id,)
    )
    if not row:
        return JSONResponse({"error": "server not found"}, status_code=404)
    server_name = row[0]["name"]

    count_row = await db.execute_fetchall(
        "SELECT COUNT(*) as cnt FROM mails WHERE server_id = ?", (server_id,)
    )
    mail_count = count_row[0]["cnt"] if count_row else 0

    asyncio.create_task(_delete_server_task(server_id, server_name, mail_count))
    return JSONResponse({"ok": True})


async def _delete_server_task(server_id, server_name: str, mail_count: int):
    conn = await open_connection()
    try:
        await broadcast(
            {
                "type": "delete_started",
                "server_id": server_id,
                "server_name": server_name,
            }
        )

        # Collect raw file paths before cascade delete removes the rows
        rows = await conn.execute_fetchall(
            "SELECT raw_path FROM mails WHERE server_id = ? AND raw_path IS NOT NULL",
            (server_id,),
        )
        raw_paths = [r["raw_path"] for r in rows]

        await conn.execute("DELETE FROM servers WHERE id = ?", (server_id,))
        await conn.commit()

        # Remove archive files
        total = len(raw_paths)
        deleted = 0
        for p in raw_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
            deleted += 1
            if deleted % 100 == 0 or deleted == total:
                await broadcast(
                    {
                        "type": "delete_progress",
                        "server_id": server_id,
                        "count": deleted,
                        "total": total,
                    }
                )

        await broadcast(
            {
                "type": "delete_completed",
                "server_id": server_id,
                "deleted": mail_count,
            }
        )
    except Exception as exc:
        logger.exception("delete_server_task failed for server %s", server_id)
        await broadcast(
            {
                "type": "delete_error",
                "server_id": server_id,
                "error": str(exc),
            }
        )
    finally:
        await conn.close()


async def delete_folder_messages(request: Request):
    """Delete all messages in a folder."""
    server_id = request.path_params["server_id"]
    folder_name = request.query_params.get("folder", "").strip()
    if not folder_name:
        return JSONResponse({"error": "folder parameter required"}, status_code=400)

    db = await get_db()

    # Look up folder
    row = await db.execute_fetchall(
        "SELECT id FROM folders WHERE server_id = ? AND name = ?",
        (server_id, folder_name),
    )
    if not row:
        return JSONResponse({"error": "folder not found"}, status_code=404)
    folder_id = row[0]["id"]

    count_row = await db.execute_fetchall(
        "SELECT COUNT(*) as cnt FROM mails WHERE folder_id = ?", (folder_id,)
    )
    mail_count = count_row[0]["cnt"] if count_row else 0

    asyncio.create_task(
        _delete_folder_task(server_id, folder_id, folder_name, mail_count)
    )
    return JSONResponse({"ok": True})


async def _delete_folder_task(
    server_id: int, folder_id: int, folder_name: str, mail_count: int
):
    conn = await open_connection()
    try:
        await broadcast(
            {
                "type": "delete_started",
                "server_id": server_id,
                "server_name": folder_name,
            }
        )

        # Collect raw file paths before deleting
        rows = await conn.execute_fetchall(
            "SELECT raw_path FROM mails WHERE folder_id = ? AND raw_path IS NOT NULL",
            (folder_id,),
        )
        raw_paths = [r["raw_path"] for r in rows]

        await conn.execute("DELETE FROM mails WHERE folder_id = ?", (folder_id,))
        await conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        await conn.execute(
            "DELETE FROM sync_state WHERE server_id = ? AND folder_name = ?",
            (server_id, folder_name),
        )
        await conn.commit()

        # Remove archive files
        total = len(raw_paths)
        deleted = 0
        for p in raw_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
            deleted += 1
            if deleted % 100 == 0 or deleted == total:
                await broadcast(
                    {
                        "type": "delete_progress",
                        "server_id": server_id,
                        "count": deleted,
                        "total": total,
                    }
                )

        await broadcast(
            {
                "type": "delete_completed",
                "server_id": server_id,
                "deleted": mail_count,
            }
        )
    except Exception as exc:
        logger.exception(
            "delete_folder_task failed for folder %s (server %s)",
            folder_name,
            server_id,
        )
        await broadcast(
            {
                "type": "delete_error",
                "server_id": server_id,
                "error": str(exc),
            }
        )
    finally:
        await conn.close()


async def search_mails(request: Request):
    server_id = request.query_params.get("server_id")
    from_q = request.query_params.get("from", "").strip()
    to_q = request.query_params.get("to", "").strip()
    subject_q = request.query_params.get("subject", "").strip()
    body_q = request.query_params.get("body", "").strip()
    date_from = request.query_params.get("date_from", "").strip()
    date_to = request.query_params.get("date_to", "").strip()
    tag_q = request.query_params.get("tag", "").strip()
    held_q = request.query_params.get("held", "").strip()

    has_dups = request.query_params.get("has_dups", "").strip()

    if not any([from_q, to_q, subject_q, body_q, date_from, date_to, tag_q, held_q, has_dups]):
        return JSONResponse({"items": [], "total": 0, "page": 0, "pageSize": PAGE_SIZE})

    conditions = []
    params = []

    if server_id:
        conditions.append("server_id = ?")
        params.append(server_id)

    if from_q:
        conditions.append("(from_name LIKE ? OR from_addr LIKE ?)")
        pattern = f"%{from_q}%"
        params.extend([pattern, pattern])

    if to_q:
        conditions.append("to_addr LIKE ?")
        params.append(f"%{to_q}%")

    if subject_q:
        conditions.append("subject LIKE ?")
        params.append(f"%{subject_q}%")

    if body_q:
        conditions.append("body_text LIKE ?")
        params.append(f"%{body_q}%")

    if date_from:
        conditions.append("date >= ?")
        params.append(date_from)

    if date_to:
        conditions.append("date <= ?")
        params.append(date_to + "T23:59:59")

    if tag_q:
        for tag in [t.strip() for t in tag_q.split(",") if t.strip()]:
            conditions.append(
                "EXISTS (SELECT 1 FROM tags t WHERE t.mail_id = mails.id AND t.tag LIKE ?)"
            )
            params.append(tag)

    if held_q:
        conditions.append("legal_hold = 1")

    if has_dups:
        conditions.append("dup_count > 0")

    where = " AND ".join(conditions)
    sort_col, _sort_col_m, sort_dir, page = _sort_params(request)

    db = await get_db()
    count_row = await db.execute_fetchall(
        f"SELECT COUNT(*) as cnt FROM mails WHERE {where}", params
    )
    total = count_row[0]["cnt"] if count_row else 0

    rows = await db.execute_fetchall(
        "SELECT id, subject, from_name, from_addr, to_addr, date, size, unread, attachment_count, legal_hold, dup_count "
        f"FROM mails WHERE {where} ORDER BY {sort_col} {sort_dir} "
        f"LIMIT {PAGE_SIZE} OFFSET {page * PAGE_SIZE}",
        params,
    )

    return JSONResponse(
        {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "pageSize": PAGE_SIZE,
        }
    )


async def list_mails(request: Request):
    server_id = request.path_params["server_id"]
    folder = request.query_params.get("folder")
    sort_col, sort_col_m, sort_dir, page = _sort_params(request)
    db = await get_db()

    if folder:
        # Check if this is a virtual (label-backed) folder
        tag_row = await db.execute_fetchall(
            "SELECT label_tag FROM folders WHERE server_id = ? AND name = ?",
            (server_id, folder),
        )
        label_tag = tag_row[0]["label_tag"] if tag_row and tag_row[0]["label_tag"] else None

        if label_tag:
            # Virtual folder — query by tag
            count_row = await db.execute_fetchall(
                "SELECT COUNT(*) as cnt FROM mails m "
                "WHERE m.server_id = ? AND EXISTS "
                "(SELECT 1 FROM tags t WHERE t.mail_id = m.id AND t.tag = ?)",
                (server_id, label_tag),
            )
            total = count_row[0]["cnt"] if count_row else 0
            rows = await db.execute_fetchall(
                "SELECT m.id, m.subject, m.from_name, m.from_addr, m.to_addr, m.date, "
                "m.size, m.unread, m.attachment_count, m.legal_hold, m.dup_count "
                "FROM mails m WHERE m.server_id = ? AND EXISTS "
                "(SELECT 1 FROM tags t WHERE t.mail_id = m.id AND t.tag = ?) "
                f"ORDER BY {sort_col_m} {sort_dir} "
                f"LIMIT {PAGE_SIZE} OFFSET {page * PAGE_SIZE}",
                (server_id, label_tag),
            )
        else:
            # Real folder — match the folder itself plus any children (separated by / or .)
            where = "m.server_id = ? AND (f.name = ? OR f.name LIKE ? OR f.name LIKE ?)"
            params = (server_id, folder, f"{folder}/%", f"{folder}.%")
            count_row = await db.execute_fetchall(
                "SELECT COUNT(*) as cnt FROM mails m JOIN folders f ON m.folder_id = f.id "
                f"WHERE {where}",
                params,
            )
            total = count_row[0]["cnt"] if count_row else 0
            rows = await db.execute_fetchall(
                "SELECT m.id, m.subject, m.from_name, m.from_addr, m.to_addr, m.date, m.size, "
                "m.unread, m.attachment_count, m.legal_hold, m.dup_count "
                "FROM mails m JOIN folders f ON m.folder_id = f.id "
                f"WHERE {where} ORDER BY {sort_col_m} {sort_dir} "
                f"LIMIT {PAGE_SIZE} OFFSET {page * PAGE_SIZE}",
                params,
            )
    else:
        where = "server_id = ?"
        params = (server_id,)
        count_row = await db.execute_fetchall(
            f"SELECT COUNT(*) as cnt FROM mails WHERE {where}", params
        )
        total = count_row[0]["cnt"] if count_row else 0
        rows = await db.execute_fetchall(
            "SELECT id, subject, from_name, from_addr, to_addr, date, size, unread, attachment_count, legal_hold, dup_count "
            f"FROM mails WHERE {where} ORDER BY {sort_col} {sort_dir} "
            f"LIMIT {PAGE_SIZE} OFFSET {page * PAGE_SIZE}",
            params,
        )

    return JSONResponse(
        {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "pageSize": PAGE_SIZE,
        }
    )


async def get_mail(request: Request):
    mail_id = request.path_params["mail_id"]
    db = await get_db()

    row = await db.execute_fetchall(
        "SELECT m.*, f.name AS folder_name, s.name AS server_name FROM mails m "
        "LEFT JOIN folders f ON m.folder_id = f.id "
        "LEFT JOIN servers s ON m.server_id = s.id WHERE m.id = ?",
        (mail_id,),
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    mail = dict(row[0])

    attachments = await db.execute_fetchall(
        "SELECT filename, content_type, size FROM attachments WHERE mail_id = ?",
        (mail_id,),
    )
    mail["attachments"] = [dict(a) for a in attachments]

    tags = await db.execute_fetchall(
        "SELECT tag FROM tags WHERE mail_id = ? ORDER BY tag", (mail_id,)
    )
    mail["tags"] = [t["tag"] for t in tags]

    return JSONResponse(mail)


async def add_tag(request: Request):
    mail_id = request.path_params["mail_id"]
    data = await request.json()
    tag = data.get("tag", "").strip()
    if not tag:
        return JSONResponse({"error": "tag required"}, status_code=400)

    request_write_lock()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO tags (mail_id, tag) VALUES (?, ?)", (mail_id, tag)
        )
        await db.commit()
    except Exception:
        pass  # duplicate tag, ignore
    return JSONResponse({"ok": True})


async def remove_tag(request: Request):
    mail_id = request.path_params["mail_id"]
    tag = request.path_params["tag"]
    request_write_lock()
    db = await get_db()
    await db.execute("DELETE FROM tags WHERE mail_id = ? AND tag = ?", (mail_id, tag))
    await db.commit()
    return JSONResponse({"ok": True})


async def delete_mail(request: Request):
    """Delete a single mail (blocked by legal_hold)."""
    mail_id = request.path_params["mail_id"]
    request_write_lock()
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT legal_hold, raw_path FROM mails WHERE id = ?", (mail_id,)
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    if row[0]["legal_hold"]:
        return JSONResponse({"error": "message is on legal hold"}, status_code=403)

    raw_path = row[0]["raw_path"]
    await db.execute("DELETE FROM mails WHERE id = ?", (mail_id,))
    await db.commit()

    if raw_path:
        try:
            Path(raw_path).unlink(missing_ok=True)
        except OSError:
            pass

    return JSONResponse({"ok": True})


async def get_mail_raw(request: Request):
    """Download the raw EML file for a mail."""
    mail_id = request.path_params["mail_id"]
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT raw_path, subject FROM mails WHERE id = ?", (mail_id,)
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    mail = row[0]
    raw_path = mail["raw_path"]

    if not raw_path:
        return JSONResponse({"error": "no raw message stored"}, status_code=404)

    try:
        data = read_raw(raw_path)
    except FileNotFoundError:
        return JSONResponse({"error": "raw message file missing"}, status_code=404)

    subject = mail["subject"] or "message"
    # Sanitize filename
    safe_name = "".join(c if c.isalnum() or c in " -_." else "_" for c in subject)[:50]
    filename = f"{safe_name}.eml"

    return Response(
        data,
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def get_mail_attachment(request: Request):
    """Download an attachment from a mail by index."""
    mail_id = request.path_params["mail_id"]
    index = request.path_params["index"]
    db = await get_db()

    row = await db.execute_fetchall(
        "SELECT raw_path FROM mails WHERE id = ?", (mail_id,)
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    raw_path = row[0]["raw_path"]
    if not raw_path:
        return JSONResponse({"error": "no raw message stored"}, status_code=404)

    try:
        filename, content_type, data = extract_attachment_from_path(raw_path, index)
    except (FileNotFoundError, IndexError) as e:
        return JSONResponse({"error": str(e)}, status_code=404)

    return Response(
        data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def get_mail_preview(request: Request):
    """Return body_text and body_html parsed from the raw EML on disk."""
    mail_id = request.path_params["mail_id"]
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT raw_path FROM mails WHERE id = ?", (mail_id,)
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    raw_path = row[0]["raw_path"]
    if not raw_path:
        return JSONResponse({"error": "no raw message stored"}, status_code=404)

    try:
        raw_bytes = read_raw(raw_path)
    except FileNotFoundError:
        return JSONResponse({"error": "raw message file missing"}, status_code=404)

    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    body_text = _extract_body(msg)
    body_html = _extract_body_html(msg)

    # Resolve cid: references to inline data URIs
    if body_html:
        cid_map = {}
        for part in msg.walk():
            cid = part.get("Content-ID", "").strip("<> ")
            if cid:
                data = part.get_payload(decode=True)
                if data:
                    ct = part.get_content_type()
                    b64 = base64.b64encode(data).decode("ascii")
                    cid_map[cid] = f"data:{ct};base64,{b64}"
        if cid_map:

            def replace_cid(m):
                ref = m.group(1)
                return cid_map.get(ref, m.group(0))

            body_html = re.sub(r'cid:([^\s"\'><]+)', replace_cid, body_html)

    raw_source = raw_bytes.decode("utf-8", errors="replace")

    return JSONResponse(
        {"body_text": body_text, "body_html": body_html, "raw_source": raw_source}
    )


async def batch_delete(request: Request):
    """Delete multiple mails, skipping any on legal hold."""
    data = await request.json()
    mail_ids = data.get("mail_ids", [])
    if not mail_ids:
        return JSONResponse({"deleted": 0, "held": 0})

    request_write_lock()
    db = await get_db()

    placeholders = ",".join("?" for _ in mail_ids)
    rows = await db.execute_fetchall(
        f"SELECT id, legal_hold, raw_path FROM mails WHERE id IN ({placeholders})",
        mail_ids,
    )

    held = 0
    to_delete = []
    raw_paths = []
    for r in rows:
        if r["legal_hold"]:
            held += 1
        else:
            to_delete.append(r["id"])
            if r["raw_path"]:
                raw_paths.append(r["raw_path"])

    if to_delete:
        del_placeholders = ",".join("?" for _ in to_delete)
        await db.execute(
            f"DELETE FROM mails WHERE id IN ({del_placeholders})", to_delete
        )
        await db.commit()

    for p in raw_paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass

    return JSONResponse({"deleted": len(to_delete), "held": held})


async def batch_tags(request: Request):
    """Add/remove tags for multiple mails."""
    data = await request.json()
    mail_ids = data.get("mail_ids", [])
    add_tags = data.get("add_tags", [])
    remove_tags = data.get("remove_tags", [])
    if not mail_ids or (not add_tags and not remove_tags):
        return JSONResponse({"updated": 0})

    request_write_lock()
    db = await get_db()

    updated = 0
    for mail_id in mail_ids:
        for tag in add_tags:
            tag = tag.strip()
            if not tag:
                continue
            try:
                await db.execute(
                    "INSERT INTO tags (mail_id, tag) VALUES (?, ?)", (mail_id, tag)
                )
                updated += 1
            except Exception:
                pass  # duplicate
        for tag in remove_tags:
            tag = tag.strip()
            if not tag:
                continue
            cursor = await db.execute(
                "DELETE FROM tags WHERE mail_id = ? AND tag = ?", (mail_id, tag)
            )
            if cursor.rowcount:
                updated += 1

    await db.commit()
    return JSONResponse({"updated": updated})


def _archive_disk_usage() -> int:
    """Return total bytes used by the archive directory on disk."""
    root = _get_archive_root()
    if not root.exists():
        return 0
    total = 0
    for f in root.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


_archive_size_cache: dict[str, int] = {"value": 0}


async def _refresh_archive_size():
    """Refresh the cached archive size in the background."""
    _archive_size_cache["value"] = await asyncio.to_thread(_archive_disk_usage)


async def get_stats(request: Request):
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT COUNT(*) as messages, "
        "COALESCE(SUM(CASE WHEN dup_count > 0 THEN 1 ELSE 0 END), 0) as duplicates, "
        "COALESCE(SUM(CASE WHEN legal_hold = 1 THEN 1 ELSE 0 END), 0) as held "
        "FROM mails"
    )
    stats = dict(row[0]) if row else {"messages": 0, "duplicates": 0, "held": 0}
    server_row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM servers")
    stats["servers"] = server_row[0]["cnt"] if server_row else 0
    stats["archive_size"] = _archive_size_cache["value"]
    # Refresh in background for next request
    asyncio.create_task(_refresh_archive_size())
    return JSONResponse(stats)


async def batch_hold(request: Request):
    """Set or release legal hold on multiple mails."""
    data = await request.json()
    mail_ids = data.get("mail_ids", [])
    hold = data.get("hold", 1)
    if not mail_ids:
        return JSONResponse({"updated": 0})

    request_write_lock()
    db = await get_db()
    placeholders = ",".join("?" for _ in mail_ids)
    cursor = await db.execute(
        f"UPDATE mails SET legal_hold = ? WHERE id IN ({placeholders})",
        [hold] + mail_ids,
    )
    await db.commit()
    return JSONResponse({"updated": cursor.rowcount})


async def toggle_hold(request: Request):
    """Toggle legal_hold between 0 and 1 for a single mail."""
    mail_id = request.path_params["mail_id"]
    request_write_lock()
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT legal_hold FROM mails WHERE id = ?", (mail_id,)
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    new_val = 0 if row[0]["legal_hold"] else 1
    await db.execute("UPDATE mails SET legal_hold = ? WHERE id = ?", (new_val, mail_id))
    await db.commit()
    return JSONResponse({"legal_hold": new_val})


async def get_version(request: Request):
    return JSONResponse({"version": __version__})


async def get_mail_duplicates(request: Request):
    """Return other mails with the same message_id or content_hash."""
    mail_id = request.path_params["mail_id"]
    sort_col, sort_col_m, sort_dir, page = _sort_params(request)
    db = await get_db()

    row = await db.execute_fetchall(
        "SELECT message_id, content_hash FROM mails WHERE id = ?", (mail_id,)
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    message_id = row[0]["message_id"]
    content_hash = row[0]["content_hash"]

    # Prefer message_id match, fall back to content_hash
    if message_id:
        match_clause = "m.message_id = ?"
        match_param = message_id
    elif content_hash:
        match_clause = "m.content_hash = ?"
        match_param = content_hash
    else:
        return JSONResponse(
            {"items": [], "total": 0, "page": 0, "pageSize": PAGE_SIZE}
        )

    where = f"{match_clause} AND m.id != ?"
    params = [match_param, mail_id]

    count_row = await db.execute_fetchall(
        f"SELECT COUNT(*) as cnt FROM mails m WHERE {where}", params
    )
    total = count_row[0]["cnt"] if count_row else 0

    rows = await db.execute_fetchall(
        "SELECT m.id, m.subject, m.from_name, m.from_addr, m.to_addr, m.date, "
        "m.size, m.unread, m.attachment_count, m.legal_hold, m.dup_count, "
        "s.name AS server_name, f.name AS folder_name "
        "FROM mails m "
        "LEFT JOIN servers s ON m.server_id = s.id "
        "LEFT JOIN folders f ON m.folder_id = f.id "
        f"WHERE {where} ORDER BY {sort_col_m} {sort_dir} "
        f"LIMIT {PAGE_SIZE} OFFSET {page * PAGE_SIZE}",
        params,
    )

    return JSONResponse(
        {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "pageSize": PAGE_SIZE,
        }
    )


async def batch_export(request: Request):
    """Export multiple mails as a zip of .eml files."""
    data = await request.json()
    mail_ids = data.get("mail_ids", [])
    if not mail_ids:
        return JSONResponse({"error": "mail_ids required"}, status_code=400)

    db = await get_db()
    placeholders = ",".join("?" for _ in mail_ids)
    rows = await db.execute_fetchall(
        f"SELECT id, subject, raw_path FROM mails WHERE id IN ({placeholders})",
        mail_ids,
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for r in rows:
            if not r["raw_path"]:
                continue
            try:
                raw = read_raw(r["raw_path"])
            except FileNotFoundError:
                continue
            subject = r["subject"] or "message"
            safe_name = "".join(
                c if c.isalnum() or c in " -_." else "_" for c in subject
            )[:50]
            zf.writestr(f"{safe_name}_{r['id']}.eml", raw)

    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="mail-hunter-export.zip"'
        },
    )
