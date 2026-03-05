import asyncio
import logging
import time

from mail_hunter.config import decrypt_password
from mail_hunter.db import open_connection
from mail_hunter.services.imap import sync_server, is_syncing, is_any_syncing

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
                    "SELECT id, name, host, port, username, password, use_ssl, sync_interval "
                    "FROM servers "
                    "WHERE protocol != 'import' AND sync_enabled = 1 "
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

            synced_any = False
            for server in ordered:
                interval = server.get("sync_interval", 15) or 15
                if interval <= 0:
                    # 0 means auto-sync disabled for this server
                    continue

                # Skip if this server was synced recently
                last = _last_sync_time.get(server["id"], 0)
                if time.monotonic() - last < interval * 60:
                    continue

                # Wait for any manual sync to finish
                while is_any_syncing():
                    await asyncio.sleep(30)

                # Decrypt and start sync
                try:
                    server["password"] = decrypt_password(server["password"])
                    logger.info(
                        "Auto-sync starting for %s (id=%d)",
                        server["name"],
                        server["id"],
                    )
                    asyncio.create_task(sync_server(server["id"], server))

                    # Wait for sync to finish
                    await asyncio.sleep(2)  # give it a moment to register
                    while is_syncing(server["id"]):
                        await asyncio.sleep(5)

                    _last_sync_time[server["id"]] = time.monotonic()
                    logger.info(
                        "Auto-sync completed for %s (id=%d)",
                        server["name"],
                        server["id"],
                    )
                    synced_any = True
                except Exception:
                    logger.exception(
                        "Auto-sync error for %s (id=%d)",
                        server["name"],
                        server["id"],
                    )

                _last_server_id = server["id"]

            # Sleep before next check — shorter if nothing was due, longer if we just did a round
            if synced_any:
                logger.info("Auto-sync round complete")
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
