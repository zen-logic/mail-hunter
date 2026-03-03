import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from mail_hunter.db import open_connection
from mail_hunter.services.importer import (
    build_server_cache,
    collect_unmatched_addresses,
    create_import_server,
    run_import,
    scan_raw_messages,
)
from mail_hunter.services.parser import parse_message

# Pending imports awaiting address resolution: import_id -> {raw_messages, temp_dir, filename}
_pending_imports: dict[str, dict] = {}


async def import_upload(request: Request):
    """Upload EML/MBOX files. Scans addresses and either starts import or asks for resolution."""
    form = await request.form()
    fmt = form.get("format", "eml")

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

    # Scan all raw messages (synchronous, fast)
    is_mbox = fmt == "mbox"
    raw_messages = await asyncio.to_thread(scan_raw_messages, file_paths, is_mbox)

    if not raw_messages:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return JSONResponse(
            {"error": "no messages found in uploaded files"}, status_code=400
        )

    # Parse all messages to get addresses
    parsed_messages = []
    for raw in raw_messages:
        try:
            parsed_messages.append(parse_message(raw))
        except Exception:
            pass

    # Check addresses against existing servers
    db = await open_connection()
    try:
        server_cache = await build_server_cache(db)
    finally:
        await db.close()

    unmatched = collect_unmatched_addresses(parsed_messages, server_cache)

    filename = files[0].filename or "file"
    if len(files) > 1:
        filename = f"{len(files)} files"

    if not unmatched:
        # All messages matched — start import immediately

        async def _task():
            conn = await open_connection()
            try:
                await run_import(conn, raw_messages, filename)
            finally:
                await conn.close()
                shutil.rmtree(temp_dir, ignore_errors=True)

        asyncio.create_task(_task())
        return JSONResponse({"ok": True, "count": len(raw_messages)})

    # Some addresses unmatched — stash and ask user
    import_id = uuid.uuid4().hex[:12]
    _pending_imports[import_id] = {
        "raw_messages": raw_messages,
        "temp_dir": temp_dir,
        "filename": filename,
    }

    # Return unmatched addresses sorted by count descending
    addr_list = [
        {"address": addr, "count": count}
        for addr, count in sorted(unmatched.items(), key=lambda x: -x[1])
    ]

    return JSONResponse(
        {
            "import_id": import_id,
            "total_messages": len(raw_messages),
            "unmatched": addr_list,
        }
    )


async def import_resolve(request: Request):
    """Resolve unmatched addresses by creating servers, then start import."""
    data = await request.json()
    import_id = data.get("import_id", "")
    create_addresses = data.get("create_servers", [])

    pending = _pending_imports.pop(import_id, None)
    if not pending:
        return JSONResponse({"error": "import not found or expired"}, status_code=404)

    raw_messages = pending["raw_messages"]
    temp_dir = pending["temp_dir"]
    filename = pending["filename"]

    async def _task():
        conn = await open_connection()
        try:
            # Create servers for the chosen addresses
            for addr in create_addresses:
                await create_import_server(conn, addr.strip().lower())

            await run_import(conn, raw_messages, filename)
        finally:
            await conn.close()
            shutil.rmtree(temp_dir, ignore_errors=True)

    asyncio.create_task(_task())
    return JSONResponse({"ok": True, "count": len(raw_messages)})
