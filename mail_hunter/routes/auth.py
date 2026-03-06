import sqlite3

from starlette.requests import Request
from starlette.responses import JSONResponse

from mail_hunter.db import get_db, execute_write
from mail_hunter.services import auth as auth_svc


async def auth_status(request: Request):
    db = await get_db()
    count = await auth_svc.user_count(db)
    return JSONResponse({"ok": True, "needsSetup": count == 0})


async def auth_setup(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    display_name = (body.get("displayName") or "").strip()

    if not username or not password:
        return JSONResponse(
            {"ok": False, "error": "Username and password are required."}, status_code=400
        )

    db = await get_db()
    count = await auth_svc.user_count(db)
    if count > 0:
        return JSONResponse(
            {"ok": False, "error": "Setup already completed."}, status_code=403
        )

    async def _create(conn, u, p, d):
        user = await auth_svc.create_user(conn, u, p, d)
        token = await auth_svc.create_session(conn, user["id"])
        return {"token": token, "user": user}

    result = await execute_write(_create, username, password, display_name)
    return JSONResponse({"ok": True, **result})


async def auth_login(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        return JSONResponse(
            {"ok": False, "error": "Username and password are required."}, status_code=400
        )

    db = await get_db()
    user = await auth_svc.authenticate(db, username, password)
    if not user:
        return JSONResponse(
            {"ok": False, "error": "Invalid username or password."}, status_code=401
        )

    async def _session(conn, uid):
        return await auth_svc.create_session(conn, uid)

    token = await execute_write(_session, user["id"])
    return JSONResponse({"ok": True, "token": token, "user": user})


async def auth_logout(request: Request):
    auth_header = request.headers.get("authorization", "")
    token = (
        auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
    )
    if token:

        async def _delete(conn, t):
            await auth_svc.delete_session(conn, t)

        await execute_write(_delete, token)
    return JSONResponse({"ok": True, "loggedOut": True})


async def auth_me(request: Request):
    user = request.scope.get("user")
    if not user:
        return JSONResponse({"ok": False, "error": "Not authenticated."}, status_code=401)
    return JSONResponse({"ok": True, **user})


async def list_users(request: Request):
    db = await get_db()
    users = await auth_svc.get_users(db)
    return JSONResponse({"ok": True, "users": users})


async def create_user(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    display_name = (body.get("displayName") or "").strip()

    if not username or not password:
        return JSONResponse(
            {"ok": False, "error": "Username and password are required."}, status_code=400
        )

    try:

        async def _create(conn, u, p, d):
            return await auth_svc.create_user(conn, u, p, d)

        user = await execute_write(_create, username, password, display_name)
        return JSONResponse({"ok": True, "user": user})
    except sqlite3.IntegrityError:
        return JSONResponse(
            {"ok": False, "error": "Username already exists."}, status_code=409
        )


async def update_user(request: Request):
    user_id = int(request.path_params["id"])
    body = await request.json()

    kwargs = {}
    if "username" in body:
        val = (body["username"] or "").strip()
        if not val:
            return JSONResponse(
                {"ok": False, "error": "Username cannot be empty."}, status_code=400
            )
        kwargs["username"] = val
    if "password" in body:
        if not body["password"]:
            return JSONResponse(
                {"ok": False, "error": "Password cannot be empty."}, status_code=400
            )
        kwargs["password"] = body["password"]
    if "displayName" in body:
        kwargs["display_name"] = (body["displayName"] or "").strip()

    if not kwargs:
        return JSONResponse(
            {"ok": False, "error": "Nothing to update."}, status_code=400
        )

    try:

        async def _update(conn, uid, **kw):
            await auth_svc.update_user(conn, uid, **kw)

        await execute_write(_update, user_id, **kwargs)
        return JSONResponse({"ok": True, "updated": True})
    except sqlite3.IntegrityError:
        return JSONResponse(
            {"ok": False, "error": "Username already exists."}, status_code=409
        )


async def delete_user(request: Request):
    user_id = int(request.path_params["id"])
    current_user = request.scope.get("user")
    if current_user and current_user["id"] == user_id:
        return JSONResponse(
            {"ok": False, "error": "Cannot delete your own account."}, status_code=400
        )

    async def _delete(conn, uid):
        await auth_svc.delete_user(conn, uid)

    await execute_write(_delete, user_id)
    return JSONResponse({"ok": True, "deleted": True})
