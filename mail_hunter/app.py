from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.responses import HTMLResponse
from pathlib import Path

from mail_hunter.db import get_db, close_db

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
    Mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static"),
]

app = Starlette(
    routes=routes,
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
)
