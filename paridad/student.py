"""The student projection selects results by the authenticated owner."""

import re
import unicodedata
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from .analysis import tokenize
from .config import CLIENT_DETERRENCE, MAX_FILE_BYTES, MAX_SCOPE_SUBMISSIONS
from .security import auth_user, check_csrf, check_rate
from .web import redirect, view

router = APIRouter()
PAGE_SIZE = 30
FILENAME = re.compile(r"\w[\w .()+-]{0,119}")


class OwnResult(BaseModel):
    filename: str
    activity: str
    language: str
    submitted_at: datetime
    state: str
    similarity_bp: int | None
    similarity_alert: bool
    ai_bp: int | None
    ai_alert: bool
    ai_declared: bool


def owned_history(conn, student_id: int, page: int):
    rows = conn.execute(
        """SELECT s.filename, a.code AS activity, l.name AS language, s.created_at,
                  s.state, s.max_similarity_bp, s.ai_bp, (s.ai_credit IS NOT NULL) AS ai_declared,
                  COALESCE(a.similarity_threshold, cfg.similarity_threshold) AS similarity_threshold,
                  COALESCE(a.ai_threshold, cfg.ai_threshold) AS ai_threshold
           FROM submissions s JOIN activities a ON a.id = s.activity_id
           JOIN languages l ON l.id = s.language_id CROSS JOIN settings cfg
           WHERE s.student_id = %s ORDER BY s.created_at DESC, s.id DESC LIMIT %s OFFSET %s""",
        (student_id, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
    ).fetchall()
    return [
        OwnResult(
            filename=row["filename"], activity=row["activity"], language=row["language"],
            submitted_at=row["created_at"], state=row["state"],
            similarity_bp=row["max_similarity_bp"] if row["state"] == "completed" else None,
            similarity_alert=row["max_similarity_bp"] > 100 * row["similarity_threshold"],
            ai_bp=row["ai_bp"], ai_declared=row["ai_declared"],
            ai_alert=(row["ai_bp"] is not None and not row["ai_declared"]
                      and row["ai_bp"] > 100 * row["ai_threshold"]),
        ) for row in rows[:PAGE_SIZE]
    ], len(rows) > PAGE_SIZE


@router.get("/student")
def dashboard(request: Request, page: Annotated[int, Query(ge=1, le=10000)] = 1):
    with request.app.state.pool.connection() as conn:
        user = auth_user(conn, request, "STUDENT")
        scopes = conn.execute(
            """SELECT a.id AS activity_id, a.code, a.title, l.id AS language_id, l.name, l.extensions
               FROM activity_languages al JOIN activities a ON a.id = al.activity_id
               JOIN languages l ON l.id = al.language_id WHERE a.is_open ORDER BY a.code, l.name"""
        ).fetchall()
        results, has_next = owned_history(conn, user["id"], page)
    activities, languages = {}, {}
    for scope in scopes:
        activity_id, language_id = scope["activity_id"], scope["language_id"]
        activities[activity_id] = f"{scope['code']} · {scope['title']}"
        entry = languages.setdefault(language_id, {
            "name": scope["name"], "extensions": ", ".join(scope["extensions"]), "activities": [],
        })
        entry["activities"].append(str(activity_id))
    return view(
        request, "student.html", user=user, activities=activities, languages=languages,
        results=results, deterrence=CLIENT_DETERRENCE, page=page, has_next=has_next, request_id=uuid4(),
    )


@router.get("/api/student/submissions")
def own_api(request: Request, page: Annotated[int, Query(ge=1, le=10000)] = 1) -> list[OwnResult]:
    with request.app.state.pool.connection() as conn:
        user = auth_user(conn, request, "STUDENT")
        results, _ = owned_history(conn, user["id"], page)
    return results


def read_text_file(file: UploadFile, extensions: list[str]) -> tuple[str, str]:
    filename = file.filename or ""
    if not FILENAME.fullmatch(filename) or PurePosixPath(filename).suffix.lower() not in extensions:
        raise HTTPException(400, "El nombre o la extensión del archivo no está permitido.")
    data = file.file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "El archivo supera los 128 KiB.")
    try:
        source = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise HTTPException(400, "Sube un archivo de texto en UTF-8.") from None
    if not source.strip() or any(
        char not in "\n\t" and unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in source
    ):
        raise HTTPException(400, "El archivo está vacío o contiene caracteres de control.")
    return filename, source


@router.post("/student/submissions")
def submit(
    request: Request,
    activity_id: Annotated[int, Form(gt=0, lt=2**63)],
    language_id: Annotated[int, Form(gt=0, lt=2**63)],
    request_id: Annotated[UUID, Form()],
    csrf: Annotated[str, Form(max_length=100)],
    file: Annotated[UploadFile, File()],
    ai_declared: Annotated[bool, Form()] = False,
    ai_credit: Annotated[str, Form(max_length=1000)] = "",
):
    pool = request.app.state.pool
    with pool.connection() as conn:
        user = auth_user(conn, request, "STUDENT")
        check_csrf(user["csrf"], csrf)
    check_rate(pool, "upload-user", str(user["id"]), 12, 600)

    ai_credit = ai_credit.strip()
    if ai_declared != bool(ai_credit) or "\x00" in ai_credit:
        raise HTTPException(400, "Indica si usaste IA e incluye el crédito cuando corresponda.")

    with pool.connection() as conn:
        user = auth_user(conn, request, "STUDENT")
        check_csrf(user["csrf"], csrf)
        user_id = user["id"]
        if conn.execute(
            "SELECT 1 FROM submissions WHERE student_id = %s AND request_id = %s", (user_id, request_id)
        ).fetchone():
            return redirect("/student")
        activity = conn.execute(
            "SELECT is_open FROM activities WHERE id = %s FOR SHARE", (activity_id,)
        ).fetchone()
        if not activity or not activity["is_open"]:
            raise HTTPException(400, "La actividad no admite entregas.")
        scope = conn.execute(
            """SELECT l.extensions, l.profile, l.options FROM activity_languages al
               JOIN languages l ON l.id = al.language_id
               WHERE al.activity_id = %s AND al.language_id = %s FOR NO KEY UPDATE OF al""",
            (activity_id, language_id),
        ).fetchone()
        if not scope:
            raise HTTPException(400, "El lenguaje seleccionado no está habilitado para la actividad.")
        filename, source = read_text_file(file, scope["extensions"])
        try:
            tokenize(source, scope["profile"], scope["options"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        count = conn.execute(
            "SELECT count(*) AS total FROM submissions WHERE activity_id = %s AND language_id = %s",
            (activity_id, language_id),
        ).fetchone()["total"]
        if count >= MAX_SCOPE_SUBMISSIONS:
            raise HTTPException(409, "La actividad alcanzó su capacidad para este lenguaje.")
        conn.execute(
            """INSERT INTO submissions (activity_id, language_id, student_id, request_id, filename, source, ai_credit)
               VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (student_id, request_id) DO NOTHING""",
            (activity_id, language_id, user_id, request_id, filename, source, ai_credit or None),
        )
    return redirect("/student")
