"""Administrative forms and access to detailed evidence."""

import re
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Query, Request
from psycopg import errors
from psycopg.types.json import Jsonb

from .analysis import COMMENT_STYLES
from .security import PASSWORDS, USERNAME, auth_user, check_csrf, normalized_username, valid_password
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


@router.get("")
def dashboard(request: Request):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        totals = conn.execute(
            """SELECT (SELECT count(*) FROM users WHERE role = 'STUDENT') AS students,
                      (SELECT count(*) FROM activities WHERE is_open) AS activities,
                      (SELECT count(*) FROM submissions WHERE state = 'pending') AS pending,
                      (SELECT count(*) FROM submissions WHERE state = 'failed') AS failed"""
        ).fetchone()
    return view(request, "admin.html", user=user, settings=settings, totals=totals)


@router.post("/settings")
def settings(request: Request, csrf: Csrf, similarity_threshold: Threshold, ai_threshold: Threshold):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request, csrf)
        conn.execute(
            "UPDATE settings SET similarity_threshold = %s, ai_threshold = %s WHERE id = 1",
            (similarity_threshold, ai_threshold),
        )
    return redirect("/admin")


@router.get("/accounts")
def accounts(request: Request, page: Page = 1):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        students = conn.execute(
            """SELECT id, username, display_name, active FROM users WHERE role = 'STUDENT'
               ORDER BY username LIMIT %s OFFSET %s""",
            (PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
        ).fetchall()
    return view(
        request, "accounts.html", user=user, students=students[:PAGE_SIZE],
        page=page, has_next=len(students) > PAGE_SIZE,
    )


@router.post("/accounts")
def create_account(
    request: Request, csrf: Csrf, username: Annotated[str, Form(max_length=64)],
    display_name: Name, password: Annotated[str, Form(max_length=128)],
):
    try:
        with request.app.state.pool.connection() as conn:
            administrator(conn, request, csrf)
            username = normalized_username(username)
            if not USERNAME.fullmatch(username) or not valid_password(password):
                raise HTTPException(400, "El usuario o la contraseña no cumple el formato. Usa al menos 12 caracteres de contraseña.")
            conn.execute(
                """INSERT INTO users (username, display_name, password_hash, role)
                   VALUES (%s, %s, %s, 'STUDENT')""",
                (username, required_text(display_name), PASSWORDS.hash(password)),
            )
    except errors.UniqueViolation:
        raise HTTPException(409, "Ese usuario ya existe.") from None
    return redirect("/admin/accounts")


@router.post("/accounts/{student_id}")
def update_account(
    request: Request, student_id: int, csrf: Csrf, display_name: Name,
    active: Annotated[bool, Form()] = False,
    new_password: Annotated[str, Form(max_length=128)] = "",
):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request, csrf)
        if not 0 < student_id < 2**63:
            raise HTTPException(404, "Cuenta no encontrada.")
        next_hash = None
        if new_password:
            if not valid_password(new_password):
                raise HTTPException(400, "La contraseña debe tener al menos 12 caracteres.")
            next_hash = PASSWORDS.hash(new_password)
        changed = conn.execute(
            """UPDATE users SET display_name = %s, active = %s, password_hash = COALESCE(%s, password_hash)
               WHERE id = %s AND role = 'STUDENT' RETURNING id""",
            (required_text(display_name), active, next_hash, student_id),
        ).fetchone()
        if not changed:
            raise HTTPException(404, "Cuenta no encontrada.")
        if not active or next_hash:
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (student_id,))
    return redirect("/admin/accounts")


@router.get("/activities")
def activities(request: Request):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        labs = conn.execute(
            """SELECT a.*, ARRAY(SELECT language_id FROM activity_languages WHERE activity_id = a.id) AS language_ids
               FROM activities a ORDER BY a.code"""
        ).fetchall()
        languages = conn.execute("SELECT * FROM languages ORDER BY name").fetchall()
    return view(
        request, "activities.html", user=user, labs=labs, languages=languages,
        comment_styles={key: label for key, label in {
            "none": "Sin comentarios", "c": "// y /* … */", "marie": "/", "arm": "//, @, ; y /* … */",
            "vhdl": "--", "hash": "#", "semicolon": ";",
        }.items() if key in COMMENT_STYLES},
    )


def update_languages(conn, activity_id: int, language_ids: list[int]) -> None:
    if not language_ids or len(language_ids) > 32 or any(not 0 < value < 2**63 for value in language_ids):
        raise HTTPException(400, "Selecciona entre 1 y 32 lenguajes válidos.")
    ids = sorted(set(language_ids))
    known = conn.execute("SELECT id FROM languages WHERE id = ANY(%s)", (ids,)).fetchall()
    if len(known) != len(ids):
        raise HTTPException(400, "Alguno de los lenguajes no existe.")
    conn.execute(
        "DELETE FROM activity_languages WHERE activity_id = %s AND NOT (language_id = ANY(%s))",
        (activity_id, ids),
    )
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO activity_languages (activity_id, language_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(activity_id, language_id) for language_id in ids],
        )


@router.post("/activities")
def create_activity(
    request: Request, csrf: Csrf, code: Code, title: Name,
    language_ids: Annotated[list[int], Form()],
    similarity_threshold: Annotated[str, Form(max_length=3)] = "",
    ai_threshold: Annotated[str, Form(max_length=3)] = "",
):
    try:
        with request.app.state.pool.connection() as conn:
            administrator(conn, request, csrf)
            row = conn.execute(
                """INSERT INTO activities (code, title, similarity_threshold, ai_threshold)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (code_value(code), required_text(title),
                 optional_threshold(similarity_threshold), optional_threshold(ai_threshold)),
            ).fetchone()
            update_languages(conn, row["id"], language_ids)
    except errors.UniqueViolation:
        raise HTTPException(409, "Ya existe una actividad con ese código.") from None
    return redirect("/admin/activities")


@router.post("/activities/{activity_id}")
def update_activity(
    request: Request, activity_id: int, csrf: Csrf, title: Name,
    language_ids: Annotated[list[int], Form()],
    is_open: Annotated[bool, Form()] = False,
    similarity_threshold: Annotated[str, Form(max_length=3)] = "",
    ai_threshold: Annotated[str, Form(max_length=3)] = "",
):
    try:
        with request.app.state.pool.connection() as conn:
            administrator(conn, request, csrf)
            if not 0 < activity_id < 2**63:
                raise HTTPException(404, "Actividad no encontrada.")
            row = conn.execute(
                """UPDATE activities SET title = %s, is_open = %s, similarity_threshold = %s, ai_threshold = %s
                   WHERE id = %s RETURNING id""",
                (required_text(title), is_open, optional_threshold(similarity_threshold),
                 optional_threshold(ai_threshold), activity_id),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Actividad no encontrada.")
            update_languages(conn, activity_id, language_ids)
    except errors.ForeignKeyViolation:
        raise HTTPException(409, "Ese lenguaje ya tiene entregas en la actividad y debe conservarse.") from None
    return redirect("/admin/activities")


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


def optional_context_id(value: str) -> int | None:
    if not value:
        return None
    if not re.fullmatch(r"[0-9]+", value) or not 0 < int(value) < 2**63:
        raise HTTPException(400, "Identificador de filtro inválido.")
    return int(value)


@router.get("/reports")
def reports(
    request: Request, activity_id: ContextQuery = "", language_id: ContextQuery = "",
    alerts_only: bool = False, page: Page = 1,
):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        labs = conn.execute("SELECT id, code, title FROM activities ORDER BY code").fetchall()
        languages = conn.execute("SELECT id, name FROM languages ORDER BY name").fetchall()
        selected_activity, selected_language = optional_context_id(activity_id), optional_context_id(language_id)
        context = (selected_activity, selected_activity, selected_language, selected_language)
        pagination = (PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)
        pairs = conn.execute(
            """SELECT p.left_id, p.right_id, p.score_bp, a.code, l.name AS language,
                      ul.display_name AS left_name, ul.username AS left_username,
                      ur.display_name AS right_name, ur.username AS right_username,
                      p.score_bp > 100 * COALESCE(a.similarity_threshold, cfg.similarity_threshold) AS alert
               FROM similarity_pairs p JOIN activities a ON a.id = p.activity_id
               JOIN languages l ON l.id = p.language_id JOIN users ul ON ul.id = p.left_student_id
               JOIN users ur ON ur.id = p.right_student_id CROSS JOIN settings cfg
               WHERE (%s::bigint IS NULL OR a.id = %s) AND (%s::bigint IS NULL OR l.id = %s)
                 AND (NOT %s OR p.score_bp > 100 * COALESCE(a.similarity_threshold, cfg.similarity_threshold))
               ORDER BY p.score_bp DESC, p.left_id, p.right_id LIMIT %s OFFSET %s""",
            (*context, alerts_only, *pagination),
        ).fetchall()
        submissions = conn.execute(
            """SELECT s.id, s.filename, s.state, s.ai_bp, s.ai_credit IS NOT NULL AS declared,
                      a.code, l.name AS language, u.display_name, u.username,
                      s.ai_credit IS NULL AND s.ai_bp > 100 * COALESCE(a.ai_threshold, cfg.ai_threshold) AS alert
               FROM submissions s JOIN activities a ON a.id = s.activity_id JOIN languages l ON l.id = s.language_id
               JOIN users u ON u.id = s.student_id CROSS JOIN settings cfg
               WHERE (%s::bigint IS NULL OR a.id = %s) AND (%s::bigint IS NULL OR l.id = %s)
                 AND (NOT %s OR (s.ai_credit IS NULL AND s.ai_bp > 100 * COALESCE(a.ai_threshold, cfg.ai_threshold)))
               ORDER BY s.created_at DESC, s.id DESC LIMIT %s OFFSET %s""",
            (*context, alerts_only, *pagination),
        ).fetchall()
    return view(
        request, "reports.html", user=user, labs=labs, languages=languages, selected_activity=selected_activity,
        selected_language=selected_language, alerts_only=alerts_only, pairs=pairs[:PAGE_SIZE], submissions=submissions[:PAGE_SIZE],
        page=page, has_next=len(pairs) > PAGE_SIZE or len(submissions) > PAGE_SIZE,
    )


def excerpt(source: str, span: list[int]) -> list[tuple[int, str]]:
    start, end = span
    return [
        (index, line[:240] + ("…" if len(line) > 240 else ""))
        for index, line in enumerate(source.split("\n")[start - 1:min(end, start + 39)], start)
    ]


@router.get("/reports/{left_id}/{right_id}")
def pair_report(request: Request, left_id: UUID, right_id: UUID):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        pair = conn.execute(
            """SELECT p.*, a.code, l.name AS language, cfg.similarity_threshold AS global_threshold,
                      a.similarity_threshold, ul.display_name AS left_name, ul.username AS left_username,
                      ur.display_name AS right_name, ur.username AS right_username,
                      sl.filename AS left_filename, sr.filename AS right_filename,
                      sl.source AS left_source, sr.source AS right_source,
                      sl.ai_bp AS left_ai_bp, sr.ai_bp AS right_ai_bp
               FROM similarity_pairs p JOIN activities a ON a.id = p.activity_id
               JOIN languages l ON l.id = p.language_id JOIN users ul ON ul.id = p.left_student_id
               JOIN users ur ON ur.id = p.right_student_id JOIN submissions sl ON sl.id = p.left_id
               JOIN submissions sr ON sr.id = p.right_id CROSS JOIN settings cfg
               WHERE p.left_id = %s AND p.right_id = %s""",
            (left_id, right_id),
        ).fetchone()
    if not pair:
        raise HTTPException(404, "Comparación no encontrada.")
    for fragment in pair["fragments"]:
        fragment["left_excerpt"] = excerpt(pair["left_source"], fragment["left"])
        fragment["right_excerpt"] = excerpt(pair["right_source"], fragment["right"])
    return view(request, "pair.html", user=user, pair=pair)


@router.get("/submissions/{submission_id}")
def submission_report(request: Request, submission_id: UUID):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        submission = conn.execute(
            """SELECT s.id, s.filename, s.source, s.state, s.ai_bp, s.ai_credit, s.ai_features,
                      s.analysis_version, s.max_similarity_bp, a.code, l.name AS language,
                      u.display_name, u.username, COALESCE(a.ai_threshold, cfg.ai_threshold) AS ai_threshold,
                      COALESCE(a.similarity_threshold, cfg.similarity_threshold) AS similarity_threshold
               FROM submissions s JOIN activities a ON a.id = s.activity_id JOIN languages l ON l.id = s.language_id
               JOIN users u ON u.id = s.student_id CROSS JOIN settings cfg WHERE s.id = %s""",
            (submission_id,),
        ).fetchone()
    if not submission:
        raise HTTPException(404, "Entrega no encontrada.")
    mail = quote(f"Revisión de tu entrega en {submission['code']}", safe="")
    body = quote(
        f"Hola,\r\n\r\nRevisa el estado de tu archivo {submission['filename']} "
        f"en la actividad {submission['code']} de Paridad. Podemos conversar sobre tu proceso de trabajo.\r\n",
        safe="",
    )
    return view(request, "submission.html", user=user, submission=submission, draft=f"mailto:?subject={mail}&body={body}")


@router.post("/submissions/{submission_id}/retry")
def retry(request: Request, submission_id: UUID, csrf: Csrf):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request, csrf)
        row = conn.execute(
            """UPDATE submissions SET state = 'pending', attempts = 0, available_at = now()
               WHERE id = %s AND state = 'failed' RETURNING id""",
            (submission_id,),
        ).fetchone()
        if not row:
            raise HTTPException(409, "Solo se puede reintentar una entrega con análisis fallido.")
    return redirect(f"/admin/submissions/{submission_id}")
