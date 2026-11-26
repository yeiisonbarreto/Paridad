"""Password hashing, sessions, CSRF and database-backed rate limiting."""

import hashlib
import ipaddress
import re
import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request
from starlette.responses import Response

from .config import (
    LOGIN_CSRF_COOKIE, SAME_SITE, SECURE_COOKIES, SESSION_COOKIE, SESSION_HOURS, TRUST_PROXY_IP,
)

USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")
PASSWORDS = PasswordHasher(type=Type.ID, time_cost=3, memory_cost=32768, parallelism=2)
DUMMY_HASH = PASSWORDS.hash(secrets.token_hex(32))


def normalized_username(value: str) -> str:
    return value.strip().lower()


def valid_password(value: str) -> bool:
    return 12 <= len(value) <= 128 and len(value.encode("utf-8")) <= 512


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return PASSWORDS.verify(password_hash or DUMMY_HASH, password) and bool(password_hash)
    except (VerificationError, InvalidHashError):
        return False


def token_digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def password_session(conn, user_id: int, response: Response) -> None:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    conn.execute(
        """INSERT INTO sessions (token_hash, user_id, csrf, expires_at)
           VALUES (%s, %s, %s, now() + make_interval(hours => %s))""",
        (token_digest(token), user_id, csrf, SESSION_HOURS),
    )
    set_cookie(response, SESSION_COOKIE, token, SESSION_HOURS * 3600)


def set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name, value, max_age=max_age, secure=SECURE_COOKIES, httponly=True, samesite=SAME_SITE, path="/",
    )


def clear_cookie(response: Response, name: str) -> None:
    set_cookie(response, name, "", 0)


def check_csrf(expected: str | None, submitted: str) -> None:
    # Compare bytes so malformed Unicode input also gets a normal rejection.
    if not expected or not secrets.compare_digest(expected.encode(), submitted.encode()):
        raise HTTPException(403, "El formulario caducó o la solicitud no es válida. Vuelve a cargar la página.")


def auth_user(conn, request: Request, role: str | None = None) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    user = None
    if token:
        user = conn.execute(
            """SELECT u.id, u.username, u.display_name, u.role, s.csrf
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = %s AND s.expires_at > now() AND u.active FOR SHARE OF u""",
            (token_digest(token),),
        ).fetchone()
    if role and not user:
        raise HTTPException(401, "Inicia sesión para continuar.")
    if role and user["role"] != role:
        raise HTTPException(403, "Tu cuenta no tiene acceso a esta página.")
    return user


def delete_current_session(conn, request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = %s", (token_digest(token),))


def source_ip(request: Request) -> str:
    value = request.headers.get("x-paridad-ip") if TRUST_PROXY_IP else None
    try:
        return str(ipaddress.ip_address(value or (request.client.host if request.client else "")))
    except ValueError:
        return "unknown"


def check_rate(pool, bucket: str, subject: str, limit: int, seconds: int) -> None:
    with pool.connection() as conn:
        row = conn.execute(
            """INSERT INTO rate_limits (bucket, subject, window_start, hits)
               VALUES (%s, %s, date_bin(make_interval(secs => %s), now(), '2000-01-01 UTC'::timestamptz), 1)
               ON CONFLICT (bucket, subject, window_start) DO UPDATE
               SET hits = least(rate_limits.hits + 1, %s)
               RETURNING hits""",
            (bucket, subject, seconds, limit + 1),
        ).fetchone()
    if row["hits"] > limit:
        raise HTTPException(429, "Alcanzaste el límite temporal de solicitudes.", headers={"Retry-After": str(seconds)})


def issue_login_challenge(response: Response, token: str) -> None:
    set_cookie(response, LOGIN_CSRF_COOKIE, token, 600)
