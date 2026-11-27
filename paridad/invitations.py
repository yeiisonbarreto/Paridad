"""Single-use student invitations and password setup."""

import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request

from .security import PASSWORDS, check_rate, source_ip, token_digest, valid_password
from .web import redirect, view

router = APIRouter()
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
INVALID_INVITATION = "Invitación vencida, revocada o ya utilizada. Solicita otra al administrador."


def create_invitation(conn, student_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        """INSERT INTO student_invitations (user_id, token_hash, expires_at)
           VALUES (%s, %s, now() + interval '24 hours')
           ON CONFLICT (user_id) DO UPDATE
           SET token_hash = excluded.token_hash, expires_at = excluded.expires_at""",
        (student_id, token_digest(token)),
    )
    return token


def find_invitation(conn, token_hash: bytes):
    return conn.execute(
        """SELECT u.id, u.username FROM student_invitations i JOIN users u ON i.user_id = u.id
           WHERE i.token_hash = %s AND i.expires_at > clock_timestamp() AND u.active AND u.role = 'STUDENT'""",
        (token_hash,),
    ).fetchone()


@router.get("/invitacion")
def invitation_form(request: Request, token: Annotated[str, Query(max_length=100)] = ""):
    if not TOKEN_PATTERN.fullmatch(token):
        raise HTTPException(400, INVALID_INVITATION)
    with request.app.state.pool.connection() as conn:
        student = find_invitation(conn, token_digest(token))
    if not student:
        raise HTTPException(400, INVALID_INVITATION)
    return view(request, "invitation.html", token=token, username=student["username"])


@router.post("/invitacion")
def activate_student(
    request: Request, token: Annotated[str, Form(max_length=100)],
    password: Annotated[str, Form(max_length=128)],
    confirmation: Annotated[str, Form(max_length=128)],
):
    if not TOKEN_PATTERN.fullmatch(token):
        raise HTTPException(400, INVALID_INVITATION)
    pool = request.app.state.pool
    check_rate(pool, "invite-activate", source_ip(request), 80, 900)
    digest = token_digest(token)
    with pool.connection() as conn:
        student = find_invitation(conn, digest)
        if not student:
            raise HTTPException(400, INVALID_INVITATION)
        if not valid_password(password) or password != confirmation:
            return view(
                request, "invitation.html", status=400, token=token, username=student["username"],
                error="Usa de 12 a 128 caracteres y repite la misma contraseña.",
            )
        # Lock the account before its invitation, as in the administrator flows.
        locked = conn.execute(
            "SELECT id FROM users WHERE id = %s AND active AND role = 'STUDENT' FOR NO KEY UPDATE",
            (student["id"],),
        ).fetchone()
        if not locked:
            raise HTTPException(400, INVALID_INVITATION)
        used = conn.execute(
            """DELETE FROM student_invitations WHERE user_id = %s AND token_hash = %s
               AND expires_at > clock_timestamp() RETURNING user_id""",
            (student["id"], digest),
        ).fetchone()
        if not used:
            raise HTTPException(400, INVALID_INVITATION)
        conn.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (PASSWORDS.hash(password), student["id"]),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = %s", (student["id"],))
    return redirect("/login")
