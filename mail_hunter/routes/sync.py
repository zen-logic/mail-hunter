import asyncio
import logging
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from mail_hunter.config import decrypt_password
from mail_hunter.db import get_db, request_write_lock
from mail_hunter.services.imap import (
    test_connection,
    sync_server,
    cancel_sync,
    claim_sync,
    clear_auto_sync_flag,
    is_auto_sync_active,
    is_any_syncing,
    is_syncing,
)
from mail_hunter.services.backfill import (
    backfill_labels,
    cancel_backfill,
    is_backfilling,
)
from mail_hunter.ws import broadcast

logger = logging.getLogger(__name__)


async def sync_endpoint(request: Request):
    """GET returns sync status, POST starts a sync."""
    server_id = request.path_params["server_id"]
    db = await get_db()

    if request.method == "GET":
        row = await db.execute_fetchall(
            "SELECT 1 FROM sync_queue WHERE server_id = ?", (server_id,)
        )
        return JSONResponse({"syncing": is_syncing(server_id), "queued": bool(row)})

    # POST — start or queue sync
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

    full = request.query_params.get("full") == "1"
    purge = request.query_params.get("purge") == "1"
    start_folder = request.query_params.get("folder")

    if not claim_sync(server_id):
        if is_auto_sync_active():
            # Pre-empt auto-sync — user takes priority
            from mail_hunter.services.imap import get_active_sync_ids
            logger.info("Sync POST server=%d → pre-empting auto-sync", server_id)
            for active_id in get_active_sync_ids():
                cancel_sync(active_id)
            clear_auto_sync_flag()
            # Wait for the cancel to take effect
            for _ in range(20):
                if not is_any_syncing():
                    break
                await asyncio.sleep(0.25)
            # Try to claim again
            if not claim_sync(server_id):
                # Still blocked — fall through to queue
                logger.info("Sync POST server=%d → pre-empt failed, queuing", server_id)
            else:
                logger.info("Sync POST server=%d → STARTING (pre-empted auto)", server_id)
                asyncio.create_task(
                    sync_server(server_id, server, full=full, start_folder=start_folder)
                )
                return JSONResponse({"ok": True})

        # Another manual sync is running — queue this one
        logger.info("Sync POST server=%d → QUEUED", server_id)
        request_write_lock()
        await db.execute(
            "INSERT INTO sync_queue (server_id, folder, full, purge) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(server_id) DO UPDATE SET "
            "folder=excluded.folder, full=excluded.full, purge=excluded.purge, "
            "created_at=datetime('now')",
            (server_id, start_folder, int(full), int(purge)),
        )
        await db.commit()
        await broadcast(
            {
                "type": "sync_queued",
                "server_id": server_id,
                "server_name": server["name"],
            }
        )
        return JSONResponse({"ok": True, "queued": True})

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

    logger.info("Sync POST server=%d → STARTING", server_id)
    asyncio.create_task(
        sync_server(server_id, server, full=full, start_folder=start_folder)
    )
    return JSONResponse({"ok": True})


async def dequeue_sync(request: Request):
    """Remove a queued sync for a server."""
    server_id = request.path_params["server_id"]
    db = await get_db()
    request_write_lock()
    cursor = await db.execute(
        "DELETE FROM sync_queue WHERE server_id = ?", (server_id,)
    )
    await db.commit()
    if cursor.rowcount == 0:
        return JSONResponse({"error": "nothing queued"}, status_code=404)
    await broadcast({"type": "sync_dequeued", "server_id": server_id})
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
