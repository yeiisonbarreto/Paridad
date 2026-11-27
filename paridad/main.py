"""FastAPI entry point for local accounts and role-specific routes."""

import secrets
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from . import accounts, admin, invitations, student
from .config import BASE_DIR, LOGIN_CSRF_COOKIE, PUBLIC_HOST, SESSION_COOKIE
from .db import make_pool
from .security import (
    USERNAME, auth_user, check_csrf, check_rate, clear_cookie, delete_current_session,
    issue_login_challenge, normalized_username, password_session, source_ip, valid_password, verify_password,
)
from .web import HTTPControls, redirect, view


@asynccontextmanager
async def lifespan(app):
    with make_pool() as pool:
        pool.wait()
        app.state.pool = pool
        yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[PUBLIC_HOST], www_redirect=False)
app.add_middleware(HTTPControls)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(student.router)
app.include_router(admin.router)
app.include_router(accounts.router)
app.include_router(invitations.router)


@app.exception_handler(HTTPException)
def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": str(exc.detail)}, exc.status_code, headers=exc.headers)
    if exc.status_code == 401:
        return redirect("/login")
    response = view(request, "error.html", status=exc.status_code, message=str(exc.detail))
    if exc.headers:
        response.headers.update(exc.headers)
    return response


@app.exception_handler(RequestValidationError)
def validation_error(request: Request, _exc: RequestValidationError):
    # FastAPI's default details can contain submitted passwords or other private data.
    return http_error(request, HTTPException(422, "Algún campo tiene un formato inválido."))


@app.get("/healthz")
def health(request: Request):
    with request.app.state.pool.connection() as conn:
        conn.execute("SELECT id FROM settings WHERE id = 1").fetchone()
    return {"status": "ok"}


def home(role: str) -> str:
    return "/admin" if role == "ADMIN" else "/student"


@app.get("/")
def index(request: Request):
    with request.app.state.pool.connection() as conn:
        user = auth_user(conn, request)
    return redirect(home(user["role"]) if user else "/login")


def login_form(request: Request, error=None):
    token = secrets.token_urlsafe(32)
    response = view(request, "login.html", csrf=token, error=error, status=400 if error else 200)
    issue_login_challenge(response, token)
    return response


@app.get("/login")
def show_login(request: Request):
    with request.app.state.pool.connection() as conn:
        user = auth_user(conn, request)
    return redirect(home(user["role"])) if user else login_form(request)


@app.post("/login")
def sign_in(
    request: Request,
    username: Annotated[str, Form(max_length=64)],
    password: Annotated[str, Form(max_length=128)],
    csrf: Annotated[str, Form(max_length=100)],
):
    check_csrf(request.cookies.get(LOGIN_CSRF_COOKIE), csrf)
    pool = request.app.state.pool
    username = normalized_username(username)
    check_rate(pool, "login-ip", source_ip(request), 80, 900)
    if not USERNAME.fullmatch(username) or not valid_password(password):
        return login_form(request, "Usuario o contraseña inválidos.")
    check_rate(pool, "login-user", username, 12, 900)
    with pool.connection() as conn:
        user = conn.execute(
            "SELECT id, role, password_hash FROM users WHERE username = %s AND active FOR SHARE",
            (username,),
        ).fetchone()
        if not verify_password(user["password_hash"] if user else None, password):
            return login_form(request, "Usuario o contraseña inválidos.")
        delete_current_session(conn, request)
        response = redirect(home(user["role"]))
        password_session(conn, user["id"], response)
    clear_cookie(response, LOGIN_CSRF_COOKIE)
    return response


@app.post("/logout")
def sign_out(request: Request, csrf: Annotated[str, Form(max_length=100)]):
    with request.app.state.pool.connection() as conn:
        user = auth_user(conn, request)
        if user:
            check_csrf(user["csrf"], csrf)
            delete_current_session(conn, request)
    response = redirect("/login")
    clear_cookie(response, SESSION_COOKIE)
    return response
