import asyncio
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from mail_hunter.config import decrypt_password
from mail_hunter.db import get_db, request_write_lock
from mail_hunter.services.imap import (
    test_connection,
    sync_server,
    cancel_sync,
    is_syncing,
)
from mail_hunter.services.backfill import (
    backfill_labels,
    cancel_backfill,
    is_backfilling,
)


async def sync_endpoint(request: Request):
    """GET returns sync status, POST starts a sync."""
    server_id = request.path_params["server_id"]

    if request.method == "GET":
        return JSONResponse({"syncing": is_syncing(server_id)})

    # POST — start sync
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, name, host, port, username, password, use_ssl, protocol FROM servers WHERE id = ?",
        (server_id,),
    )
    if not rows:
        return JSONResponse({"error": "not found"}, status_code=404)

    server = dict(rows[0])
    server["password"] = decrypt_password(server["password"])

    if server["protocol"] == "import":
        return JSONResponse(
            {"error": "import-only server, cannot sync"}, status_code=400
        )

    if is_syncing(server_id):
        return JSONResponse({"error": "sync already in progress"}, status_code=409)

    full = request.query_params.get("full") == "1"
    purge = request.query_params.get("purge") == "1"

    if purge:
        request_write_lock()
        # Delete all mails and archive files for this server
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
        full = True  # purge implies full

    start_folder = request.query_params.get("folder")
    asyncio.create_task(
        sync_server(server_id, server, full=full, start_folder=start_folder)
    )
    return JSONResponse({"ok": True})


async def stop_sync(request: Request):
    """Cancel an in-progress sync."""
    server_id = request.path_params["server_id"]
    if not is_syncing(server_id):
        return JSONResponse({"error": "no sync in progress"}, status_code=404)
    cancel_sync(server_id)
    return JSONResponse({"ok": True})


async def backfill_labels_endpoint(request: Request):
    """Start a Gmail label backfill for a server."""
    server_id = request.path_params["server_id"]
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, name, host, port, username, password, use_ssl, protocol FROM servers WHERE id = ?",
        (server_id,),
    )
    if not rows:
        return JSONResponse({"error": "not found"}, status_code=404)

    server = dict(rows[0])
    server["password"] = decrypt_password(server["password"])

    if server["protocol"] == "import":
        return JSONResponse(
            {"error": "import-only server, cannot backfill"}, status_code=400
        )

    if is_backfilling(server_id):
        return JSONResponse({"error": "backfill already in progress"}, status_code=409)

    asyncio.create_task(backfill_labels(server_id, server))
    return JSONResponse({"ok": True})


async def stop_backfill(request: Request):
    """Cancel an in-progress backfill."""
    server_id = request.path_params["server_id"]
    if not is_backfilling(server_id):
        return JSONResponse({"error": "no backfill in progress"}, status_code=404)
    cancel_backfill(server_id)
    return JSONResponse({"ok": True})


async def test_server_connection(request: Request):
    """Test IMAP connection for a server."""
    server_id = request.path_params["server_id"]
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT host, port, username, password, use_ssl FROM servers WHERE id = ?",
        (server_id,),
    )
    if not rows:
        return JSONResponse({"error": "not found"}, status_code=404)

    server = dict(rows[0])
    server["password"] = decrypt_password(server["password"])
    result = await test_connection(
        server["host"],
        server["port"],
        server["username"],
        server["password"],
        server.get("use_ssl", True),
    )
    if result.get("ok") and "is_gmail" in result:
        request_write_lock()
        await db.execute(
            "UPDATE servers SET is_gmail = ? WHERE id = ?",
            (1 if result["is_gmail"] else 0, server_id),
        )
        await db.commit()
    return JSONResponse(result)
