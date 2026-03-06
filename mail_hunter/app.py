import logging

from starlette.applications import Starlette
from starlette.routing import Route, Mount, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.responses import HTMLResponse
from pathlib import Path

from cryptography.fernet import InvalidToken

from mail_hunter.config import decrypt_password, encrypt_password
from mail_hunter.db import get_db, close_db
from mail_hunter.ws import ws_endpoint, broadcast
from mail_hunter.routes.api import (
    list_servers,
    create_server,
    update_server,
    delete_server,
    delete_folder_messages,
    search_mails,
    list_mails,
    get_mail,
    delete_mail,
    get_mail_raw,
    get_mail_preview,
    get_mail_attachment,
    add_tag,
    remove_tag,
    batch_delete,
    batch_tags,
    batch_hold,
    batch_export,
    toggle_hold,
    get_stats,
    get_server_stats,
    get_version,
    get_mail_duplicates,
    get_mail_thread,
    list_saved_searches,
    create_saved_search,
    delete_saved_search,
)
from mail_hunter.routes.import_mail import import_upload
from mail_hunter.routes.sync import (
    sync_endpoint,
    stop_sync,
    dequeue_sync,
    test_server_connection,
    backfill_labels_endpoint,
    stop_backfill,
)
from mail_hunter.services.imap import enqueue, start_next, _queue, _sync_diag
from mail_hunter.services.auto_sync import start_auto_sync, stop_auto_sync

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def homepage(request):
    index = STATIC_DIR / "index.html"
    return HTMLResponse(index.read_text())


logger = logging.getLogger(__name__)


async def _migrate_plaintext_passwords(db):
    """Re-encrypt any plaintext passwords left from before encryption was added."""
    rows = await db.execute_fetchall("SELECT id, password FROM servers")
    for r in rows:
        if not r["password"]:
            continue
        try:
            decrypt_password(r["password"])
        except InvalidToken:
            # Not valid Fernet — still plaintext, encrypt it
            encrypted = encrypt_password(r["password"])
            await db.execute(
                "UPDATE servers SET password = ? WHERE id = ?", (encrypted, r["id"])
            )
    await db.commit()


async def on_startup():
    db = await get_db()

    # Migrate any plaintext passwords to encrypted form
    await _migrate_plaintext_passwords(db)

    # Servers marked syncing=1 were interrupted — queue them for resume
    rows = await db.execute_fetchall(
        "SELECT id, name, protocol FROM servers WHERE syncing = 1"
    )
    _sync_diag.info(
        "=== SERVER STARTUP — servers with syncing=1: %s ===",
        [dict(r)["id"] for r in rows],
    )
    for r in rows:
        server = dict(r)
        if server["protocol"] == "import":
            await db.execute(
                "UPDATE servers SET syncing = 0 WHERE id = ?", (server["id"],)
            )
            await db.commit()
            continue
        _sync_diag.info("startup queuing server=%d (%s)", server["id"], server["name"])
        await db.execute("UPDATE servers SET syncing = 0 WHERE id = ?", (server["id"],))
        enqueue({
            "server_id": server["id"],
            "server_name": server["name"],
            "folder": None,
            "full": 0,
            "purge": 0,
            "priority": 0,
        })
    await db.commit()

    # Also load any entries already in sync_queue (from previous _persist_queue)
    q_rows = await db.execute_fetchall(
        "SELECT sq.server_id, s.name, sq.folder, sq.full, sq.purge, sq.priority "
        "FROM sync_queue sq JOIN servers s ON s.id = sq.server_id "
        "ORDER BY sq.priority ASC, sq.created_at ASC"
    )
    for qr in q_rows:
        enqueue({
            "server_id": qr["server_id"],
            "server_name": qr["name"],
            "folder": qr["folder"],
            "full": qr["full"],
            "purge": qr["purge"],
            "priority": qr["priority"],
        })

    # Broadcast queue state for WS replay
    for entry in _queue:
        await broadcast(
            {
                "type": "sync_queued",
                "server_id": entry["server_id"],
                "server_name": entry["server_name"],
            }
        )

    await start_next()
    start_auto_sync()


async def on_shutdown():
    stop_auto_sync()
    await close_db()


routes = [
    Route("/", homepage),
    WebSocketRoute("/ws", ws_endpoint),
    # Server sub-routes (more specific paths first)
    Route("/api/servers/{server_id:int}/sync", sync_endpoint, methods=["GET", "POST"]),
    Route("/api/servers/{server_id:int}/sync/cancel", stop_sync, methods=["POST"]),
    Route("/api/servers/{server_id:int}/sync/queue", dequeue_sync, methods=["DELETE"]),
    Route(
        "/api/servers/{server_id:int}/backfill",
        backfill_labels_endpoint,
        methods=["POST"],
    ),
    Route(
        "/api/servers/{server_id:int}/backfill/cancel", stop_backfill, methods=["POST"]
    ),
    Route(
        "/api/servers/{server_id:int}/test", test_server_connection, methods=["POST"]
    ),
    Route(
        "/api/servers/{server_id:int}/folders",
        delete_folder_messages,
        methods=["DELETE"],
    ),
    Route("/api/servers/{server_id:int}/mails", list_mails, methods=["GET"]),
    # Server CRUD
    Route("/api/servers", list_servers, methods=["GET"]),
    Route("/api/servers", create_server, methods=["POST"]),
    Route("/api/servers/{server_id:int}", update_server, methods=["PUT"]),
    Route("/api/servers/{server_id:int}", delete_server, methods=["DELETE"]),
    # Mail routes — batch endpoints before {mail_id} to avoid path conflicts
    Route("/api/mails/search", search_mails, methods=["GET"]),
    Route("/api/mails/batch/delete", batch_delete, methods=["POST"]),
    Route("/api/mails/batch/tags", batch_tags, methods=["POST"]),
    Route("/api/mails/batch/hold", batch_hold, methods=["POST"]),
    Route("/api/mails/batch/export", batch_export, methods=["POST"]),
    Route("/api/mails/{mail_id:int}", get_mail, methods=["GET"]),
    Route("/api/mails/{mail_id:int}", delete_mail, methods=["DELETE"]),
    Route("/api/mails/{mail_id:int}/raw", get_mail_raw, methods=["GET"]),
    Route("/api/mails/{mail_id:int}/preview", get_mail_preview, methods=["GET"]),
    Route(
        "/api/mails/{mail_id:int}/attachments/{index:int}",
        get_mail_attachment,
        methods=["GET"],
    ),
    Route(
        "/api/mails/{mail_id:int}/duplicates",
        get_mail_duplicates,
        methods=["GET"],
    ),
    Route(
        "/api/mails/{mail_id:int}/thread",
        get_mail_thread,
        methods=["GET"],
    ),
    Route("/api/mails/{mail_id:int}/hold", toggle_hold, methods=["PUT"]),
    Route("/api/mails/{mail_id:int}/tags", add_tag, methods=["POST"]),
    Route("/api/mails/{mail_id:int}/tags/{tag:str}", remove_tag, methods=["DELETE"]),
    # Saved searches
    Route("/api/searches", list_saved_searches, methods=["GET"]),
    Route("/api/searches", create_saved_search, methods=["POST"]),
    Route("/api/searches/{search_id:int}", delete_saved_search, methods=["DELETE"]),
    # Stats / version
    Route("/api/stats/servers", get_server_stats, methods=["GET"]),
    Route("/api/stats", get_stats, methods=["GET"]),
    Route("/api/version", get_version, methods=["GET"]),
    # Import
    Route("/api/import", import_upload, methods=["POST"]),
    Mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static"),
]

app = Starlette(
    routes=routes,
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
)
