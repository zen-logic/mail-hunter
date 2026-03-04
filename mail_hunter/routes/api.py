from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from mail_hunter.db import get_db
from mail_hunter.services.store import extract_attachment_from_path

SORT_COLUMNS = {
    "from": "from_name",
    "subject": "subject",
    "date": "date",
    "size": "size",
}
PAGE_SIZE = 100


def _sort_params(request):
    """Extract validated sort/page params from query string."""
    sort_key = request.query_params.get("sort", "date")
    sort_col = SORT_COLUMNS.get(sort_key, "date")
    sort_dir = "ASC" if request.query_params.get("sortDir") == "asc" else "DESC"
    try:
        page = max(0, int(request.query_params.get("page", 0)))
    except (ValueError, TypeError):
        page = 0
    return sort_col, sort_dir, page


async def list_servers(request: Request):
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, name, host, port, username, last_sync FROM servers ORDER BY name"
    )
    servers = []
    for r in rows:
        folders = await db.execute_fetchall(
            "SELECT f.id, f.name, COUNT(m.id) as count "
            "FROM folders f LEFT JOIN mails m ON m.folder_id = f.id "
            "WHERE f.server_id = ? GROUP BY f.id ORDER BY f.name",
            (r["id"],),
        )
        servers.append(
            {
                "id": r["id"],
                "name": r["name"],
                "host": r["host"],
                "port": r["port"],
                "username": r["username"],
                "last_sync": r["last_sync"],
                "folders": [{"name": f["name"], "count": f["count"]} for f in folders],
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

    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO servers (name, host, port, username, password) VALUES (?, ?, ?, ?, ?)",
        (name or host, host, port, username, password),
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

    if not host or not username:
        return JSONResponse({"error": "host and username required"}, status_code=400)

    db = await get_db()
    if password:
        await db.execute(
            "UPDATE servers SET name=?, host=?, port=?, username=?, password=? WHERE id=?",
            (name or host, host, port, username, password, server_id),
        )
    else:
        await db.execute(
            "UPDATE servers SET name=?, host=?, port=?, username=? WHERE id=?",
            (name or host, host, port, username, server_id),
        )
    await db.commit()
    return JSONResponse({"ok": True})


async def delete_server(request: Request):
    server_id = request.path_params["server_id"]
    db = await get_db()

    # Collect raw file paths before cascade delete removes the rows
    rows = await db.execute_fetchall(
        "SELECT raw_path FROM mails WHERE server_id = ? AND raw_path IS NOT NULL",
        (server_id,),
    )
    raw_paths = [r["raw_path"] for r in rows]

    await db.execute("DELETE FROM servers WHERE id = ?", (server_id,))
    await db.commit()

    # Remove archive files
    for p in raw_paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass

    return JSONResponse({"ok": True})


async def search_mails(request: Request):
    server_id = request.query_params.get("server_id")
    from_q = request.query_params.get("from", "").strip()
    to_q = request.query_params.get("to", "").strip()
    subject_q = request.query_params.get("subject", "").strip()
    body_q = request.query_params.get("body", "").strip()
    date_from = request.query_params.get("date_from", "").strip()
    date_to = request.query_params.get("date_to", "").strip()

    if not any([from_q, to_q, subject_q, body_q, date_from, date_to]):
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
        conditions.append("body_preview LIKE ?")
        params.append(f"%{body_q}%")

    if date_from:
        conditions.append("date >= ?")
        params.append(date_from)

    if date_to:
        conditions.append("date <= ?")
        params.append(date_to + "T23:59:59")

    where = " AND ".join(conditions)
    sort_col, sort_dir, page = _sort_params(request)

    db = await get_db()
    count_row = await db.execute_fetchall(
        f"SELECT COUNT(*) as cnt FROM mails WHERE {where}", params
    )
    total = count_row[0]["cnt"] if count_row else 0

    rows = await db.execute_fetchall(
        "SELECT id, subject, from_name, from_addr, date, size, unread, attachment_count "
        f"FROM mails WHERE {where} ORDER BY {sort_col} {sort_dir} "
        f"LIMIT {PAGE_SIZE} OFFSET {page * PAGE_SIZE}",
        params,
    )

    return JSONResponse(
        {"items": [dict(r) for r in rows], "total": total, "page": page, "pageSize": PAGE_SIZE}
    )


async def list_mails(request: Request):
    server_id = request.path_params["server_id"]
    folder = request.query_params.get("folder")
    sort_col, sort_dir, page = _sort_params(request)
    db = await get_db()

    if folder:
        where = "m.server_id = ? AND f.name = ?"
        params = (server_id, folder)
        count_row = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM mails m JOIN folders f ON m.folder_id = f.id "
            f"WHERE {where}",
            params,
        )
        total = count_row[0]["cnt"] if count_row else 0
        rows = await db.execute_fetchall(
            "SELECT m.id, m.subject, m.from_name, m.from_addr, m.date, m.size, "
            "m.unread, m.attachment_count "
            "FROM mails m JOIN folders f ON m.folder_id = f.id "
            f"WHERE {where} ORDER BY m.{sort_col} {sort_dir} "
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
            "SELECT id, subject, from_name, from_addr, date, size, unread, attachment_count "
            f"FROM mails WHERE {where} ORDER BY {sort_col} {sort_dir} "
            f"LIMIT {PAGE_SIZE} OFFSET {page * PAGE_SIZE}",
            params,
        )

    return JSONResponse(
        {"items": [dict(r) for r in rows], "total": total, "page": page, "pageSize": PAGE_SIZE}
    )


async def get_mail(request: Request):
    mail_id = request.path_params["mail_id"]
    db = await get_db()

    row = await db.execute_fetchall(
        "SELECT m.*, f.name AS folder_name FROM mails m "
        "LEFT JOIN folders f ON m.folder_id = f.id WHERE m.id = ?",
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
    db = await get_db()
    await db.execute("DELETE FROM tags WHERE mail_id = ? AND tag = ?", (mail_id, tag))
    await db.commit()
    return JSONResponse({"ok": True})


async def delete_mail(request: Request):
    """Delete a single mail (blocked by legal_hold)."""
    mail_id = request.path_params["mail_id"]
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
        data = Path(raw_path).read_bytes()
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
