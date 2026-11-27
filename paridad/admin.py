"""Administrative forms and access to detailed evidence."""

import re
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from psycopg import errors
from psycopg.types.json import Jsonb

from .analysis import COMMENT_STYLES
from .dashboard import activity_metrics
from .security import (
    PASSWORDS, USERNAME, auth_user, check_csrf, normalized_username, password_session, valid_password, verify_password,
)
from .web import redirect, view

router = APIRouter(prefix="/admin")
CODE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
EXTENSION = re.compile(r"\.[a-z0-9][a-z0-9_+-]{0,11}")
KEYWORD = re.compile(r"[^\W\d]\w*")
PAGE_SIZE = 40
Csrf = Annotated[str, Form(max_length=100)]
Code = Annotated[str, Form(max_length=32)]
Name = Annotated[str, Form(max_length=120)]
Threshold = Annotated[int, Form(ge=0, le=100)]
Page = Annotated[int, Query(ge=1, le=10000)]
ContextQuery = Annotated[str, Query(max_length=19)]
SearchQuery = Annotated[str, Query(max_length=120)]
SearchForm = Annotated[str, Form(max_length=120)]


def clean_student_search(value: str) -> str:
    if "\x00" in value:
        raise HTTPException(400, "Escribe un nombre o ID válido.")
    return value.strip()


def student_match(alias: str) -> str:
    """Match the account ID or display name literally. The alias comes from the query."""
    return f"strpos(lower({alias}.display_name || chr(10) || {alias}.username), lower(%s)) > 0"


def administrator(conn, request, csrf=None):
    user = auth_user(conn, request, "ADMIN")
    if csrf is not None:
        check_csrf(user["csrf"], csrf)
    return user


def required_text(value: str) -> str:
    value = value.strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise HTTPException(400, "Escribe un nombre válido.")
    return value


def code_value(code: str) -> str:
    code = code.strip().lower()
    if not CODE.fullmatch(code):
        raise HTTPException(400, "Usa un código que comience con letra y contenga letras, números, guiones o guiones bajos.")
    return code


def optional_threshold(value: str):
    if not value:
        return None
    if not re.fullmatch(r"[0-9]{1,3}", value) or int(value) > 100:
        raise HTTPException(400, "El umbral debe ser un entero entre 0 y 100.")
    return int(value)


def optional_context_id(value: str) -> int | None:
    if not value:
        return None
    if not re.fullmatch(r"[0-9]+", value) or not 0 < int(value) < 2**63:
        raise HTTPException(400, "Identificador de filtro inválido.")
    return int(value)


@router.get("")
def dashboard(
    request: Request, module_id: ContextQuery = "", activity_id: ContextQuery = "", language_id: ContextQuery = "",
):
    with request.app.state.pool.connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        user = administrator(conn, request)
        modules = conn.execute("SELECT id, code, title FROM modules ORDER BY code").fetchall()
        activities = conn.execute("SELECT id, module_id, code FROM activities ORDER BY code").fetchall()
        languages = conn.execute("SELECT id, name FROM languages ORDER BY name").fetchall()
        module = optional_context_id(module_id)
        activity = optional_context_id(activity_id)
        language = optional_context_id(language_id)
        totals, participation = activity_metrics(conn, module, activity, language)
    return view(
        request, "admin.html", user=user, totals=totals, participation=participation,
        modules=modules, activities=activities, languages=languages,
        selected_module=module, selected_activity=activity, selected_language=language,
    )


@router.get("/settings")
def settings_form(request: Request):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        values = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    return view(request, "admin-settings.html", user=user, settings=values)


@router.post("/settings")
def settings(request: Request, csrf: Csrf, similarity_threshold: Threshold, ai_threshold: Threshold):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request, csrf)
        conn.execute(
            "UPDATE settings SET similarity_threshold = %s, ai_threshold = %s WHERE id = 1",
            (similarity_threshold, ai_threshold),
        )
        conn.execute(
            """UPDATE activities SET similarity_threshold = NULL, ai_threshold = NULL
               WHERE similarity_threshold IS NOT NULL OR ai_threshold IS NOT NULL"""
        )
    return redirect("/admin")


@router.post("/languages")
def create_language(
    request: Request, csrf: Csrf, code: Code, name: Annotated[str, Form(max_length=80)],
    extensions: Annotated[str, Form(max_length=180)],
    comments: Annotated[str, Form(max_length=12)] = "none",
    keywords: Annotated[str, Form(max_length=3000)] = "",
    case_insensitive: Annotated[bool, Form()] = False,
):
    try:
        with request.app.state.pool.connection() as conn:
            administrator(conn, request, csrf)
            suffixes = sorted({extension.strip().lower() for extension in extensions.split(",") if extension.strip()})
            words = sorted(set(keywords.lower().split() if case_insensitive else keywords.split()))
            if not 1 <= len(suffixes) <= 12 or any(not EXTENSION.fullmatch(ext) for ext in suffixes):
                raise HTTPException(400, "Escribe entre 1 y 12 extensiones como .txt, separadas por comas.")
            if comments not in COMMENT_STYLES or len(words) > 120 or any(
                len(word) > 40 or not KEYWORD.fullmatch(word) for word in words
            ):
                raise HTTPException(400, "Revisa el estilo de comentarios y las palabras clave.")
            conn.execute(
                """INSERT INTO languages (code, name, profile, extensions, options)
                   VALUES (%s, %s, 'generic', %s, %s)""",
                (code_value(code), required_text(name), suffixes, Jsonb({
                    "comments": comments, "keywords": words, "case_insensitive": case_insensitive,
                })),
            )
    except errors.UniqueViolation:
        raise HTTPException(409, "Ya existe un lenguaje con ese código.") from None
    return redirect("/admin/activities")


@router.get("/perfil")
def profile(request: Request, guardado: bool = False):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
    return view(request, "profile.html", user=user, saved=guardado)


@router.post("/perfil")
def update_profile(
    request: Request, csrf: Csrf,
    new_username: Annotated[str, Form(max_length=64)],
    current_password: Annotated[str, Form(max_length=128)],
    new_password: Annotated[str, Form(max_length=128)] = "",
    confirmation: Annotated[str, Form(max_length=128)] = "",
):
    pool = request.app.state.pool
    with pool.connection() as conn:
        user_id = administrator(conn, request, csrf)["id"]

    username = normalized_username(new_username)
    if not USERNAME.fullmatch(username):
        raise HTTPException(400, "Escribe un nombre de usuario válido.")
    if (new_password or confirmation) and (not valid_password(new_password) or new_password != confirmation):
        raise HTTPException(400, "Usa de 12 a 128 caracteres para la contraseña nueva y repítela.")

    try:
        with pool.connection() as conn:
            # Serialize credential changes before checking the session again.
            locked = conn.execute(
                "SELECT password_hash FROM users WHERE id = %s AND active AND role = 'ADMIN' FOR UPDATE",
                (user_id,),
            ).fetchone()
            user = administrator(conn, request, csrf)
            if not locked or not verify_password(locked["password_hash"], current_password):
                return view(request, "profile.html", user=user, status=403, error="La contraseña actual es incorrecta.")
            if username == user["username"] and not new_password:
                return view(request, "profile.html", user=user, status=400, error="Escribe un usuario o una contraseña nueva.")
            conn.execute(
                "UPDATE users SET username = %s, password_hash = COALESCE(%s, password_hash) WHERE id = %s",
                (username, PASSWORDS.hash(new_password) if new_password else None, user_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
            response = redirect("/admin/perfil?guardado=true")
            password_session(conn, user_id, response)
    except errors.UniqueViolation:
        raise HTTPException(409, "Ese nombre de usuario ya está en uso.") from None
    return response
