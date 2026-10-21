"""Protection for the requests that change data (POST, PUT, PATCH, DELETE).

Always on:
- the request must come from the dashboard's own pages: a browser's Origin (or Referer) must
  name this host, so another web page open in the same browser cannot change anything;
- the body must be JSON or a PDF, which a foreign page cannot send without the browser asking
  this server first (and it never says yes).

Optional: WRITE_PASSWORD in .env. Then every change also needs the password once per browser
(POST /api/login sets an HttpOnly cookie for 180 days); reading stays open in the home network.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
BODY_TYPES = ("application/json", "application/pdf")
COOKIE = "pv_write"
OPEN_PATHS = {"/api/login", "/api/logout"}

router = APIRouter()


def password() -> str:
    return os.environ.get("WRITE_PASSWORD", "")


def token(pw: str) -> str:
    return hmac.new(pw.encode(), b"deyehome-write-cookie", hashlib.sha256).hexdigest()


def same_origin(request: Request) -> bool:
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return True   # not a browser page (curl, scripts on the same machine)
    if source == "null":
        return False
    return urlsplit(source).netloc.lower() == (request.headers.get("host") or "").lower()


def has_password(request: Request) -> bool:
    pw = password()
    if not pw:
        return True
    given = request.headers.get("x-write-password")
    if given is not None:
        return hmac.compare_digest(given.encode(), pw.encode())
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), token(pw))


async def guard_writes(request: Request, call_next):
    if request.method in WRITE_METHODS:
        if not same_origin(request):
            return JSONResponse({"detail": "Промени се приемат само от страниците на дашборда"}, status_code=403)
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype and ctype not in BODY_TYPES:
            return JSONResponse({"detail": "Приемат се само JSON или PDF"}, status_code=415)
        if request.url.path not in OPEN_PATHS and not has_password(request):
            return JSONResponse({"detail": "Нужна е паролата за промени", "code": "password_required"}, status_code=401)
    return await call_next(request)


class Login(BaseModel):
    password: str


@router.get("/api/login")
def login_state(request: Request):
    return {"password_set": bool(password()), "ok": has_password(request)}


@router.post("/api/login")
def login(body: Login):
    pw = password()
    if not pw:
        return {"ok": True, "password_set": False}
    if not hmac.compare_digest(body.password.encode(), pw.encode()):
        raise HTTPException(401, "Грешна парола")
    resp = JSONResponse({"ok": True, "password_set": True})
    resp.set_cookie(COOKIE, token(pw), max_age=180 * 86400, httponly=True, samesite="strict")
    return resp


@router.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp
