"""The student projection selects results by the authenticated owner."""

import re
import unicodedata
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from .analysis import tokenize
from .config import CLIENT_DETERRENCE, MAX_FILE_BYTES, MAX_SCOPE_SUBMISSIONS
from .delivery import database_time, delivery_status
from .security import auth_user, check_csrf, check_rate
from .web import redirect, view

router = APIRouter()
PAGE_SIZE = 30
MAX_STUDENT_FILES = 3
FILENAME = re.compile(r"\w[\w .()+-]{0,119}")


class OwnResult(BaseModel):
    id: UUID
    grade: int | None
    clarification_status: str | None
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


def owned_history(conn, student_id: int, page: int, module_id: int | None = None):
    rows = conn.execute(
        """SELECT s.id, s.filename, a.code AS activity, l.name AS language, s.created_at,
                  COALESCE(s.grade_override, s.automatic_grade) AS grade, c.status AS clarification_status,
                  s.state, s.max_similarity_bp, s.ai_bp, (s.ai_credit IS NOT NULL) AS ai_declared,
                  COALESCE(a.similarity_threshold, cfg.similarity_threshold) AS similarity_threshold,
                  COALESCE(a.ai_threshold, cfg.ai_threshold) AS ai_threshold
           FROM submissions s JOIN activities a ON a.id = s.activity_id
           JOIN languages l ON l.id = s.language_id LEFT JOIN grade_clarifications c ON c.submission_id = s.id
           CROSS JOIN settings cfg
           WHERE s.student_id = %s AND (%s::bigint IS NULL OR a.module_id = %s)
           ORDER BY s.created_at DESC, s.id DESC LIMIT %s OFFSET %s""",
        (student_id, module_id, module_id, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
    ).fetchall()
    return [
        OwnResult(
            id=row["id"], grade=row["grade"], clarification_status=row["clarification_status"],
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
def dashboard(
    request: Request, page: Annotated[int, Query(ge=1, le=10000)] = 1,
    module_id: Annotated[int | None, Query(gt=0, lt=2**63)] = None,
    historial: Literal["modulo", "todos"] = "modulo", entrega: Literal["recibida"] | None = None,
):
    with request.app.state.pool.connection() as conn:
        user = auth_user(conn, request, "STUDENT")
        modules = conn.execute(
            """SELECT m.* FROM modules m WHERE EXISTS (SELECT 1 FROM activities a WHERE a.module_id = m.id)
               ORDER BY m.code""",
        ).fetchall()
        chosen = next((module for module in modules if module["id"] == module_id), None)
        now = database_time(conn)
        deliveries = conn.execute(
            """SELECT a.*, ARRAY(
                   SELECT l.name || ' (' || array_to_string(l.extensions, ', ') || ')'
                   FROM activity_languages al JOIN languages l ON l.id = al.language_id
                   WHERE al.activity_id = a.id ORDER BY l.name) AS formats
               FROM activities a WHERE a.module_id = %s ORDER BY a.code""", (module_id,),
        ).fetchall() if chosen else []
        for delivery in deliveries:
            delivery["status"] = delivery_status(delivery, now)
        history_module = chosen["id"] if chosen and historial == "modulo" else None
        results, has_next = owned_history(conn, user["id"], page, history_module)
        count = upload_count(conn, user["id"])
    return view(
        request, "student.html", user=user, modules=modules, chosen=chosen, deliveries=deliveries, now=now,
        results=results, upload_count=count, upload_limit=MAX_STUDENT_FILES, deterrence=CLIENT_DETERRENCE,
        page=page, has_next=has_next, history_module=history_module, received=entrega == "recibida",
    )


def upload_count(conn, student_id: int) -> int:
    return conn.execute("SELECT count(*) AS total FROM submissions WHERE student_id = %s",
                        (student_id,)).fetchone()["total"]


@router.get("/student/actividades/{activity_id}/subir")
def upload_form(request: Request, activity_id: int):
    with request.app.state.pool.connection() as conn:
        user = auth_user(conn, request, "STUDENT")
        activity = None
        if 0 < activity_id < 2**63:
            activity = conn.execute("SELECT * FROM activities WHERE id = %s", (activity_id,)).fetchone()
        if activity is None:
            raise HTTPException(404, "Actividad no encontrada.")
        now = database_time(conn)
        languages = conn.execute(
            """SELECT l.id, l.name, l.extensions FROM activity_languages al
               JOIN languages l ON l.id = al.language_id WHERE al.activity_id = %s ORDER BY l.name""",
            (activity_id,),
        ).fetchall()
        count = upload_count(conn, user["id"])
    activity["status"] = delivery_status(activity, now)
    return view(request, "student-upload.html", user=user, activity=activity, languages=languages, now=now,
                upload_count=count, upload_limit=MAX_STUDENT_FILES, request_id=uuid4())


@router.get("/api/student/submissions")
def own_api(request: Request, page: Annotated[int, Query(ge=1, le=10000)] = 1) -> list[OwnResult]:
    with request.app.state.pool.connection() as conn:
        user = auth_user(conn, request, "STUDENT")
        results, _ = owned_history(conn, user["id"], page)
    return results


def read_text_file(file: UploadFile, extensions: list[str]) -> tuple[str, str, bytes]:
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
    return filename, source, data


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
        if not conn.execute(
            "SELECT id FROM users WHERE id = %s AND active FOR NO KEY UPDATE", (user_id,),
        ).fetchone():
            raise HTTPException(403, "La cuenta está inhabilitada.")
        previous = conn.execute(
            """SELECT a.module_id FROM submissions s JOIN activities a ON a.id = s.activity_id
               WHERE s.student_id = %s AND s.request_id = %s""", (user_id, request_id),
        ).fetchone()
        if previous:
            return redirect(f"/student?module_id={previous['module_id']}&entrega=recibida")
        activity = conn.execute(
            "SELECT module_id, is_open, opens_at, closes_at FROM activities WHERE id = %s FOR SHARE", (activity_id,)
        ).fetchone()
        if not activity or not activity["is_open"]:
            raise HTTPException(400, "La actividad no admite entregas.")
        check_delivery_window(activity, database_time(conn))
        scope = conn.execute(
            """SELECT l.extensions, l.profile, l.options FROM activity_languages al
               JOIN languages l ON l.id = al.language_id
               WHERE al.activity_id = %s AND al.language_id = %s FOR NO KEY UPDATE OF al""",
            (activity_id, language_id),
        ).fetchone()
        if not scope:
            raise HTTPException(400, "El lenguaje seleccionado no está habilitado para la actividad.")
        filename, source, source_bytes = read_text_file(file, scope["extensions"])
        try:
            tokenize(source, scope["profile"], scope["options"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if upload_count(conn, user_id) >= MAX_STUDENT_FILES:
            raise HTTPException(409, "Alcanzaste el máximo de tres archivos.")
        count = conn.execute(
            "SELECT count(*) AS total FROM submissions WHERE activity_id = %s AND language_id = %s",
            (activity_id, language_id),
        ).fetchone()["total"]
        if count >= MAX_SCOPE_SUBMISSIONS:
            raise HTTPException(409, "La actividad alcanzó su capacidad para este lenguaje.")
        check_delivery_window(activity, database_time(conn))
        conn.execute(
            """INSERT INTO submissions (activity_id, language_id, student_id, request_id, filename, source, source_bytes, ai_credit)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (student_id, request_id) DO NOTHING""",
            (activity_id, language_id, user_id, request_id, filename, source, source_bytes, ai_credit or None),
        )
    return redirect(f"/student?module_id={activity['module_id']}&entrega=recibida")


def check_delivery_window(activity: dict, now: datetime) -> None:
    status = delivery_status(activity, now)
    if status == "scheduled":
        raise HTTPException(400, "El plazo de esta actividad todavía no ha comenzado.")
    if status == "expired":
        raise HTTPException(400, "El plazo de esta actividad ya terminó.")
