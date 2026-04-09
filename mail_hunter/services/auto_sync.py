import asyncio
import logging
import time

from mail_hunter.db import open_connection
from mail_hunter.services import imap as imap_svc
from mail_hunter.services.imap import (
    _queue,
    enqueue,
    start_next,
    _persist_queue,
    _sync_diag,
)
from mail_hunter.ws import broadcast

logger = logging.getLogger(__name__)

_auto_sync_task: asyncio.Task | None = None
_last_server_id: int = 0
# server_id -> timestamp of last sync completion
_last_sync_time: dict[int, float] = {}


async def _auto_sync_loop():
    global _last_server_id

    # Let resumed syncs settle on startup
    await asyncio.sleep(5)

    while True:
        try:
            # Fetch eligible servers with their per-server interval
            db = await open_connection()
            try:
                rows = await db.execute_fetchall(
                    "SELECT id, name, sync_interval "
                    "FROM servers "
                    "WHERE protocol NOT IN ('import', 'archive') AND sync_enabled = 1 "
                    "ORDER BY id"
                )
                eligible = [dict(r) for r in rows]
            finally:
                await db.close()

            if not eligible:
                await asyncio.sleep(60)
                continue

            # Build round-robin order starting after _last_server_id
            after = [s for s in eligible if s["id"] > _last_server_id]
            before = [s for s in eligible if s["id"] <= _last_server_id]
            ordered = after + before

            queued_any = False
            for server in ordered:
                interval = server.get("sync_interval", 15) or 15
                if interval <= 0:
                    continue

                # Skip if this server was synced recently
                last = _last_sync_time.get(server["id"], 0)
                if time.monotonic() - last < interval * 60:
                    continue

                # Don't queue if a sync is active or queue already has entries
                if imap_svc._slot is not None:
                    break
                if _queue:
                    break

                _sync_diag.info(
                    "auto_sync queuing server=%d (%s)",
                    server["id"],
                    server["name"],
                )
                enqueue({
                    "server_id": server["id"],
                    "server_name": server["name"],
                    "folder": None,
                    "full": 0,
                    "purge": 0,
                    "priority": 10,  # auto = low priority
                })
                await broadcast(
                    {
                        "type": "sync_queued",
                        "server_id": server["id"],
                        "server_name": server["name"],
                    }
                )
                _last_sync_time[server["id"]] = time.monotonic()
                _last_server_id = server["id"]
                queued_any = True
                await start_next()
                asyncio.create_task(_persist_queue())  # fire-and-forget
                break  # One at a time

            if queued_any:
                logger.info("Auto-sync queued server %d", _last_server_id)

            await asyncio.sleep(60)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Auto-sync loop error, retrying in 60s")
            await asyncio.sleep(60)


def start_auto_sync():
    global _auto_sync_task
    if _auto_sync_task is None or _auto_sync_task.done():
        _auto_sync_task = asyncio.create_task(_auto_sync_loop())
        logger.info("Auto-sync started")


def stop_auto_sync():
    global _auto_sync_task
    if _auto_sync_task and not _auto_sync_task.done():
        _auto_sync_task.cancel()
        logger.info("Auto-sync stopped")
    _auto_sync_task = None
