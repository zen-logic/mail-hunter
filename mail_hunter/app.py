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
    get_mail_attachment,
    add_tag,
    remove_tag,
)
from mail_hunter.routes.import_mail import import_upload, import_resolve
from mail_hunter.routes.sync import sync_endpoint, stop_sync, test_server_connection

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def homepage(request):
    index = STATIC_DIR / "index.html"
    return HTMLResponse(index.read_text())


async def on_startup():
    await get_db()


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
    Route(
        "/api/mails/{mail_id:int}/attachments/{index:int}",
        get_mail_attachment,
        methods=["GET"],
    ),
    Route("/api/mails/{mail_id:int}/tags", add_tag, methods=["POST"]),
    Route("/api/mails/{mail_id:int}/tags/{tag:str}", remove_tag, methods=["DELETE"]),
    # Import
    Route("/api/import", import_upload, methods=["POST"]),
    Route("/api/import/resolve", import_resolve, methods=["POST"]),
    Mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static"),
]

app = Starlette(
    routes=routes,
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
)
