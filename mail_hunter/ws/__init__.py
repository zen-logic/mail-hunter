import json
import logging
from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

clients: set[WebSocket] = set()


async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    logger.info("WebSocket client connected (%d total)", len(clients))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(websocket)
        logger.info("WebSocket client disconnected (%d total)", len(clients))


async def broadcast(msg: dict):
    payload = json.dumps(msg)
    dead = []
    for ws in clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)
