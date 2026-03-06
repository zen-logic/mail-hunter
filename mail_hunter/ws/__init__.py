import json
import logging
from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

clients: set[WebSocket] = set()

# Last sync message per server_id, so new clients get current state
_sync_state: dict[int, dict] = {}


async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    logger.info("WebSocket client connected (%d total)", len(clients))
    # Replay current sync state
    for msg in _sync_state.values():
        try:
            await websocket.send_text(json.dumps(msg))
        except Exception:
            pass
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(websocket)
        logger.info("WebSocket client disconnected (%d total)", len(clients))


async def broadcast(msg: dict):
    # Track sync state for replay on reconnect
    msg_type = msg.get("type", "")
    server_id = msg.get("server_id")
    if server_id is not None and msg_type.startswith("sync_"):
        if msg_type in ("sync_completed", "sync_error", "sync_cancelled"):
            _sync_state.pop(server_id, None)
        else:
            _sync_state[server_id] = msg
            if msg_type == "sync_started":
                # Clean up queued state — the server is now syncing
                _sync_state.pop(f"q-{server_id}", None)
    if server_id is not None and msg_type in ("sync_queued", "sync_dequeued"):
        if msg_type == "sync_dequeued":
            _sync_state.pop(f"q-{server_id}", None)
        else:
            _sync_state[f"q-{server_id}"] = msg
    if server_id is not None and msg_type.startswith("backfill_"):
        if msg_type in ("backfill_completed", "backfill_error", "backfill_cancelled"):
            _sync_state.pop(f"bf-{server_id}", None)
        else:
            _sync_state[f"bf-{server_id}"] = msg

    payload = json.dumps(msg)
    dead = []
    for ws in list(clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)
