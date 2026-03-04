import asyncio
import logging

from starlette.applications import Starlette
from starlette.routing import Route, Mount, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.responses import HTMLResponse
from pathlib import Path

from mail_hunter.db import get_db, close_db
from mail_hunter.ws import ws_endpoint
from mail_hunter.routes.api import (
    list_servers,
    create_server,
    update_server,
    delete_server,
    search_mails,
    list_mails,
    get_mail,
    delete_mail,
    get_mail_raw,
    get_mail_preview,
    get_mail_attachment,
    add_tag,
    remove_tag,
    get_stats,
    get_version,
)
from mail_hunter.routes.import_mail import import_upload
from mail_hunter.routes.sync import sync_endpoint, stop_sync, test_server_connection
from mail_hunter.services.imap import sync_server

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def homepage(request):
    index = STATIC_DIR / "index.html"
    return HTMLResponse(index.read_text())


logger = logging.getLogger(__name__)


async def on_startup():
    db = await get_db()

    # Resume any syncs that were in progress when the server last stopped
    rows = await db.execute_fetchall(
        "SELECT id, name, host, port, username, password, use_ssl, protocol "
        "FROM servers WHERE syncing = 1"
    )
    for r in rows:
        server = dict(r)
        if server["protocol"] == "import":
            # Can't sync import-only servers, clear the flag
            await db.execute(
                "UPDATE servers SET syncing = 0 WHERE id = ?", (server["id"],)
            )
            await db.commit()
            continue
        logger.info("Resuming sync for server %s (id=%d)", server["name"], server["id"])
        asyncio.create_task(sync_server(server["id"], server))


async def on_shutdown():
    await close_db()


routes = [
    Route("/", homepage),
    WebSocketRoute("/ws", ws_endpoint),
    # Server sub-routes (more specific paths first)
    Route("/api/servers/{server_id:int}/sync", sync_endpoint, methods=["GET", "POST"]),
    Route("/api/servers/{server_id:int}/sync/cancel", stop_sync, methods=["POST"]),
    Route(
        "/api/servers/{server_id:int}/test", test_server_connection, methods=["POST"]
    ),
    Route("/api/servers/{server_id:int}/mails", list_mails, methods=["GET"]),
    # Server CRUD
    Route("/api/servers", list_servers, methods=["GET"]),
    Route("/api/servers", create_server, methods=["POST"]),
    Route("/api/servers/{server_id:int}", update_server, methods=["PUT"]),
    Route("/api/servers/{server_id:int}", delete_server, methods=["DELETE"]),
    # Mail routes
    Route("/api/mails/search", search_mails, methods=["GET"]),
    Route("/api/mails/{mail_id:int}", get_mail, methods=["GET"]),
    Route("/api/mails/{mail_id:int}", delete_mail, methods=["DELETE"]),
    Route("/api/mails/{mail_id:int}/raw", get_mail_raw, methods=["GET"]),
    Route("/api/mails/{mail_id:int}/preview", get_mail_preview, methods=["GET"]),
    Route(
        "/api/mails/{mail_id:int}/attachments/{index:int}",
        get_mail_attachment,
        methods=["GET"],
    ),
    Route("/api/mails/{mail_id:int}/tags", add_tag, methods=["POST"]),
    Route("/api/mails/{mail_id:int}/tags/{tag:str}", remove_tag, methods=["DELETE"]),
    # Stats / version
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
