import asyncio
import shutil
import tempfile
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from mail_hunter.db import open_connection
from mail_hunter.services.importer import (
    create_import_server,
    run_import,
    scan_raw_messages,
)


async def import_upload(request: Request):
    """Upload EML/MBOX files. Creates an import server from the filename and starts import."""
    form = await request.form()

    # Collect uploaded files
    files = form.getlist("files")
    if not files:
        return JSONResponse({"error": "no files uploaded"}, status_code=400)

    temp_dir = tempfile.mkdtemp(prefix="mh_import_")
    file_paths = []
    for f in files:
        content = await f.read()
        if not content:
            continue
        dest = Path(temp_dir) / (f.filename or "message.eml")
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            n = 1
            while dest.exists():
                dest = Path(temp_dir) / f"{stem}_{n}{suffix}"
                n += 1
        dest.write_bytes(content)
        file_paths.append(str(dest))

    if not file_paths:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return JSONResponse({"error": "no valid files"}, status_code=400)

    # Scan all raw messages (synchronous, fast — auto-detects mbox)
    raw_messages = await asyncio.to_thread(scan_raw_messages, file_paths)

    if not raw_messages:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return JSONResponse(
            {"error": "no messages found in uploaded files"}, status_code=400
        )

    server_name = form.get("server_name", "") or "Import"

    filename = files[0].filename or "file"
    if len(files) > 1:
        filename = f"{len(files)} files"

    async def _task():
        conn = await open_connection()
        try:
            server_id = await create_import_server(conn, server_name)
            await run_import(conn, raw_messages, server_id, filename)
        finally:
            await conn.close()
            shutil.rmtree(temp_dir, ignore_errors=True)

    asyncio.create_task(_task())
    return JSONResponse({"ok": True, "count": len(raw_messages)})
