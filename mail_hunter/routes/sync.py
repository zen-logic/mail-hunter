import asyncio
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from mail_hunter.config import decrypt_password
from mail_hunter.db import get_db, request_write_lock
from mail_hunter.services import imap as imap_svc
from mail_hunter.services.imap import (
    test_connection,
    cancel_sync,
    is_syncing,
    is_auto_sync_active,
    get_auto_sync_server_id,
    enqueue,
    dequeue,
    start_next,
    _persist_queue,
    _queue,
    _sync_diag,
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
        queued = any(e["server_id"] == server_id for e in _queue)
        return JSONResponse({"syncing": is_syncing(server_id), "queued": queued})

    # POST — everything goes through the queue
    rows = await db.execute_fetchall(
        "SELECT id, name, host, port, username, password, use_ssl, protocol FROM servers WHERE id = ?",
        (server_id,),
    )
    if not rows:
        return JSONResponse({"error": "not found"}, status_code=404)

    server = dict(rows[0])

    if server["protocol"] == "import":
        return JSONResponse(
            {"error": "import-only server, cannot sync"}, status_code=400
        )

    full = request.query_params.get("full") == "1"
    purge = request.query_params.get("purge") == "1"
    start_folder = request.query_params.get("folder")

    entry = {
        "server_id": server_id,
        "server_name": server["name"],
        "folder": start_folder,
        "full": int(full),
        "purge": int(purge),
        "priority": 0,  # manual = high priority
    }
    enqueue(entry)
    await broadcast(
        {"type": "sync_queued", "server_id": server_id, "server_name": server["name"]}
    )

    # If auto-sync holds the slot, cancel it so manual starts faster
    if is_auto_sync_active():
        auto_id = get_auto_sync_server_id()
        _sync_diag.info(
            "sync_endpoint server=%d cancelling auto-sync %d", server_id, auto_id
        )
        cancel_sync(auto_id)

    await start_next()  # claims slot if free, starts sync
    asyncio.create_task(_persist_queue())  # fire-and-forget — crash recovery only

    logger.info("Sync POST server=%d — slot=%s", server_id, imap_svc._slot)
    return JSONResponse({"ok": True, "queued": not is_syncing(server_id)})


async def dequeue_sync(request: Request):
    """Remove a queued sync for a server."""
    server_id = request.path_params["server_id"]
    if not dequeue(server_id):
        return JSONResponse({"error": "nothing queued"}, status_code=404)
    await broadcast({"type": "sync_dequeued", "server_id": server_id})
    asyncio.create_task(_persist_queue())  # fire-and-forget
    return JSONResponse({"ok": True})


async def stop_sync(request: Request):
    """Cancel an in-progress sync."""
    server_id = request.path_params["server_id"]
    if not is_syncing(server_id):
        return JSONResponse({"error": "no sync in progress"}, status_code=404)
    cancel_sync(server_id)
    await broadcast({"type": "sync_cancelled", "server_id": server_id})
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
