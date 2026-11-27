"""Administrative reports, grouped similarities and file management."""

import re
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from starlette.responses import Response, StreamingResponse

from .admin import ContextQuery, Csrf, PAGE_SIZE, Page, administrator
from .bulk import consume_deletion, deletion_preview
from .file_archives import submission_bytes, submission_zip
from .grade_routes import get_grade
from .notices import enqueue_manual, notices_enabled
from .report_excel import MAX_SHEET_ROWS, XLSX_MEDIA_TYPE, Percentage, report_excel
from .similarity_groups import similarity_groups
from .submission_records import delete_submissions, lock_submission_scopes
from .web import redirect, view

router = APIRouter(prefix="/admin")

SUBMISSION_LIST = """
    SELECT s.id, s.filename, s.state, s.ai_bp, s.ai_credit IS NOT NULL AS declared,
           a.code, l.name AS language, u.display_name, u.username,
           s.max_similarity_bp, s.ai_credit, s.automatic_grade, s.grade_override,
           COALESCE(s.grade_override, s.automatic_grade) AS grade, COALESCE(a.ai_threshold, cfg.ai_threshold) AS ai_threshold,
           COALESCE(a.similarity_threshold, cfg.similarity_threshold) AS similarity_threshold,
           s.ai_credit IS NULL AND s.ai_bp > 100 * COALESCE(a.ai_threshold, cfg.ai_threshold) AS alert
    FROM submissions s JOIN activities a ON a.id = s.activity_id JOIN languages l ON l.id = s.language_id
    JOIN users u ON u.id = s.student_id CROSS JOIN settings cfg
"""


SUBMISSION_FILTER = """
    WHERE (%s::bigint IS NULL OR a.id = %s) AND (%s::bigint IS NULL OR l.id = %s)
      AND (NOT %s OR s.max_similarity_bp > 100 * COALESCE(a.similarity_threshold, cfg.similarity_threshold)
          OR (s.ai_credit IS NULL AND s.ai_bp > 100 * COALESCE(a.ai_threshold, cfg.ai_threshold)))
"""

PAIR_LIST = """
    SELECT p.left_id, p.right_id, p.score_bp, a.code, l.name AS language,
           ul.display_name AS left_name, ul.username AS left_username, sl.filename AS left_filename,
           ur.display_name AS right_name, ur.username AS right_username, sr.filename AS right_filename,
           COALESCE(a.similarity_threshold, cfg.similarity_threshold) AS similarity_threshold,
           p.score_bp > 100 * COALESCE(a.similarity_threshold, cfg.similarity_threshold) AS alert
    FROM similarity_pairs p JOIN activities a ON a.id = p.activity_id
    JOIN languages l ON l.id = p.language_id JOIN users ul ON ul.id = p.left_student_id
    JOIN users ur ON ur.id = p.right_student_id
    JOIN submissions sl ON sl.id = p.left_id JOIN submissions sr ON sr.id = p.right_id CROSS JOIN settings cfg
    WHERE (%s::bigint IS NULL OR a.id = %s) AND (%s::bigint IS NULL OR l.id = %s)
      AND (NOT %s OR p.score_bp > 100 * COALESCE(a.similarity_threshold, cfg.similarity_threshold))
"""


def optional_context_id(value: str) -> int | None:
    if not value:
        return None
    if not re.fullmatch(r"[0-9]+", value) or not 0 < int(value) < 2**63:
        raise HTTPException(400, "Identificador de filtro inválido.")
    return int(value)


def filter_choices(conn, activity_id: str, language_id: str) -> dict:
    return {
        "labs": conn.execute("SELECT id, code, title FROM activities ORDER BY code").fetchall(),
        "languages": conn.execute("SELECT id, name FROM languages ORDER BY name").fetchall(),
        "selected_activity": optional_context_id(activity_id),
        "selected_language": optional_context_id(language_id),
    }


def context_params(filters: dict) -> tuple:
    activity, language = filters["selected_activity"], filters["selected_language"]
    return activity, activity, language, language


def submission_page(conn, context, page: int, *, alerts_only: bool = False):
    return conn.execute(
        SUBMISSION_LIST + SUBMISSION_FILTER + " ORDER BY s.created_at DESC, s.id DESC LIMIT %s OFFSET %s",
        (*context, alerts_only, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
    ).fetchall()


def group_page(conn, filters, context, page: int):
    with conn.cursor(name="report_similarity_groups") as cursor:
        cursor.itersize = 800
        cursor.execute(
            """SELECT p.activity_id, p.language_id, p.left_id, p.right_id, p.score_bp
               FROM similarity_pairs p JOIN activities a ON a.id = p.activity_id CROSS JOIN settings cfg
               WHERE (%s::bigint IS NULL OR p.activity_id = %s)
                 AND (%s::bigint IS NULL OR p.language_id = %s)
                 AND p.score_bp > 100 * COALESCE(a.similarity_threshold, cfg.similarity_threshold)
               ORDER BY p.activity_id, p.language_id""",
            context,
        )
        groups = sorted(
            similarity_groups(cursor),
            key=lambda group: (-group.max_score, group.activity_id, group.language_id, group.members[0][0]),
        )
    offset = (page - 1) * PAGE_SIZE
    window = groups[offset:offset + PAGE_SIZE]
    if not window:
        return [], False
    ids = [item for group in window for item, _ in group.members]
    files = {
        row["id"]: row
        for row in conn.execute(SUBMISSION_LIST + "WHERE s.id = ANY(%s)", (ids,)).fetchall()
    }
    codes = {lab["id"]: lab["code"] for lab in filters["labs"]}
    languages = {language["id"]: language["name"] for language in filters["languages"]}
    result = [
        {
            "code": codes[group.activity_id],
            "language": languages[group.language_id],
            "max_score": group.max_score,
            "files": [dict(files[item], group_score=score) for item, score in group.members],
        }
        for group in window
    ]
    return result, offset + len(window) < len(groups)


@router.get("/reports")
def reports(
    request: Request, activity_id: ContextQuery = "", language_id: ContextQuery = "",
    alerts_only: bool = False, page: Page = 1,
):
    with request.app.state.pool.connection() as conn:
        # Use one snapshot for groups and their members, including during deletions.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        user = administrator(conn, request)
        filters = filter_choices(conn, activity_id, language_id)
        context = context_params(filters)
        groups, groups_next = group_page(conn, filters, context, page)
        pairs = conn.execute(
            PAIR_LIST + " ORDER BY p.score_bp DESC, p.left_id, p.right_id LIMIT %s OFFSET %s",
            (*context, alerts_only, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
        ).fetchall()
        submissions = submission_page(conn, context, page, alerts_only=alerts_only)
    return view(
        request, "reports.html", user=user, **filters, alerts_only=alerts_only, groups=groups,
        pairs=pairs[:PAGE_SIZE], submissions=submissions[:PAGE_SIZE], page=page,
        has_next=groups_next or len(pairs) > PAGE_SIZE or len(submissions) > PAGE_SIZE,
        mail_ready=notices_enabled(),
    )


def comparison_export_rows(conn, parameters):
    with conn.cursor(name="report_excel_pairs") as cursor:
        cursor.itersize = 400
        cursor.execute(PAIR_LIST + " ORDER BY p.score_bp DESC, p.left_id, p.right_id LIMIT %s",
                       (*parameters, MAX_SHEET_ROWS - 1))
        for item in cursor:
            yield (
                item["code"], item["language"], item["left_username"], item["left_name"], item["left_filename"],
                item["right_username"], item["right_name"], item["right_filename"],
                Percentage(item["score_bp"]), Percentage(item["similarity_threshold"] * 100),
                "Revisión" if item["alert"] else "Dentro del umbral",
            )


def submission_export_rows(conn, parameters):
    with conn.cursor(name="report_excel_submissions") as cursor:
        cursor.itersize = 400
        cursor.execute(SUBMISSION_LIST + SUBMISSION_FILTER + " ORDER BY s.created_at DESC, s.id DESC LIMIT %s",
                       (*parameters, MAX_SHEET_ROWS - 1))
        for item in cursor:
            completed = item["state"] == "completed"
            yield (
                item["code"], item["language"], item["username"], item["display_name"], item["filename"],
                {"completed": "Analizado", "pending": "En análisis", "failed": "Análisis fallido"}[item["state"]],
                Percentage(item["max_similarity_bp"]) if completed else None,
                Percentage(item["similarity_threshold"] * 100),
                Percentage(item["ai_bp"]) if completed else None, Percentage(item["ai_threshold"] * 100),
                "Sí" if item["declared"] else "No", item["ai_credit"],
                "Revisión" if item["alert"] else None, item["automatic_grade"], item["grade_override"], item["grade"],
            )


def export_workbook(pool, parameters):
    with pool.connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        sheets = (
            (
                "Comparaciones",
                ("Actividad", "Lenguaje", "Usuario A", "Estudiante A", "Archivo A", "Usuario B", "Estudiante B",
                 "Archivo B", "Similitud", "Umbral de similitud", "Estado"),
                comparison_export_rows(conn, parameters),
            ),
            (
                "Entregas",
                ("Actividad", "Lenguaje", "Usuario", "Estudiante", "Archivo", "Estado",
                 "Similitud máxima", "Umbral de similitud", "Indicio de IA", "Umbral de IA", "IA declarada",
                 "Crédito de IA", "Revisión de IA", "Nota automática (0–50)", "Nota docente (0–50)", "Nota final (0–50)"),
                submission_export_rows(conn, parameters),
            ),
        )
        yield from report_excel(sheets)


@router.get("/reports/exportar")
def export_reports(request: Request, activity_id: ContextQuery = "", language_id: ContextQuery = "",
                   alerts_only: bool = False):
    pool = request.app.state.pool
    with pool.connection() as conn:
        administrator(conn, request)
        activity, language = optional_context_id(activity_id), optional_context_id(language_id)
        parameters = (activity, activity, language, language, alerts_only)
        for query in (PAIR_LIST, SUBMISSION_LIST + SUBMISSION_FILTER):
            count = conn.execute(
                f"SELECT count(*) AS total FROM ({query} LIMIT %s) AS report_rows",
                (*parameters, MAX_SHEET_ROWS),
            ).fetchone()["total"]
            if count >= MAX_SHEET_ROWS:
                raise HTTPException(413, "El informe supera el tamaño de una hoja de Excel. Aplica filtros.")
    return StreamingResponse(
        export_workbook(pool, parameters), media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="paridad-informes.xlsx"'},
    )


@router.get("/submissions")
def submissions_list(request: Request, activity_id: ContextQuery = "", language_id: ContextQuery = "", page: Page = 1):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        filters = filter_choices(conn, activity_id, language_id)
        submissions = submission_page(conn, context_params(filters), page)
    return view(
        request, "submissions.html", user=user, **filters, submissions=submissions[:PAGE_SIZE],
        page=page, has_next=len(submissions) > PAGE_SIZE, mail_ready=notices_enabled(),
    )


@router.post("/avisos")
def send_review_notices(
    request: Request, csrf: Csrf,
    selection: Annotated[list[UUID], Form()] = [], all_results: Annotated[bool, Form()] = False,
    activity_id: Annotated[str, Form(max_length=19)] = "",
    language_id: Annotated[str, Form(max_length=19)] = "", alerts_only: Annotated[bool, Form()] = False,
    destination: Annotated[Literal["reports", "submissions"], Form()] = "reports",
):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request, csrf)
        if not notices_enabled():
            raise HTTPException(409, "Configura SMTP y el remitente para enviar avisos.")
        activity, language = optional_context_id(activity_id), optional_context_id(language_id)
        ids = None if all_results else sorted(set(selection))
        rows = conn.execute(
            """SELECT s.id, s.student_id FROM submissions s
               JOIN activities a ON a.id = s.activity_id JOIN languages l ON l.id = s.language_id
               CROSS JOIN settings cfg""" + SUBMISSION_FILTER + " AND (%s::uuid[] IS NULL OR s.id = ANY(%s))",
            (activity, activity, language, language, alerts_only, ids, ids),
        ).fetchall()
        if not rows:
            raise HTTPException(400, "Selecciona al menos un archivo.")
        if ids is not None and len(rows) != len(ids):
            raise HTTPException(409, "La selección cambió. Vuelve a seleccionar los archivos.")
        students = sorted({row["student_id"] for row in rows})
        queued = enqueue_manual(conn, students)
    return view(request, "notices-result.html", user=user, queued=queued, skipped=len(students) - queued,
                destination="/admin/" + destination)


def submission_metadata(conn, submission_id: UUID):
    submission = conn.execute(
        """SELECT s.id, s.filename, s.activity_id, s.language_id, u.display_name, u.username
           FROM submissions s JOIN users u ON u.id = s.student_id WHERE s.id = %s""",
        (submission_id,),
    ).fetchone()
    if not submission:
        raise HTTPException(404, "Entrega no encontrada.")
    return submission


@router.post("/submissions/lote")
def bulk_submissions(
    request: Request, csrf: Csrf, action: Annotated[Literal["zip", "delete"], Form()],
    selection: Annotated[list[UUID], Form()] = [], all_results: Annotated[bool, Form()] = False,
    activity_id: Annotated[str, Form(max_length=19)] = "",
    language_id: Annotated[str, Form(max_length=19)] = "",
):
    pool = request.app.state.pool
    with pool.connection() as conn:
        user = administrator(conn, request, csrf)
        activity, language = optional_context_id(activity_id), optional_context_id(language_id)
        requested = None if all_results else sorted(set(selection))
        ids = [row["id"] for row in conn.execute(
            """SELECT id FROM submissions WHERE (%s::bigint IS NULL OR activity_id = %s)
                 AND (%s::bigint IS NULL OR language_id = %s)
                 AND (%s::uuid[] IS NULL OR id = ANY(%s)) ORDER BY id""",
            (activity, activity, language, language, requested, requested),
        )]
        if requested is not None and ids != requested:
            raise HTTPException(409, "La selección cambió. Vuelve a seleccionar los archivos.")
        if not ids:
            raise HTTPException(400, "Selecciona al menos un archivo.")
        if action == "delete":
            return deletion_preview(conn, request, user, "submissions", ids)
    return StreamingResponse(
        submission_zip(pool, ids), media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="paridad-archivos.zip"'},
    )


@router.post("/submissions/lote/eliminar")
def confirm_bulk_submission_deletion(request: Request, csrf: Csrf, token: Annotated[UUID, Form()]):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request, csrf)
        ids = [UUID(item) for item in consume_deletion(conn, user["id"], "submissions", token)]
        lock_submission_scopes(conn, ids)
        delete_submissions(conn, ids)
    return redirect("/admin/submissions")


@router.get("/submissions/{submission_id}/descargar")
def download_submission(request: Request, submission_id: UUID):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request)
        submission = conn.execute(
            "SELECT filename, source_bytes, source FROM submissions WHERE id = %s",
            (submission_id,),
        ).fetchone()
    if not submission:
        raise HTTPException(404, "Entrega no encontrada.")
    contents = submission_bytes(submission)
    filename = quote(submission["filename"], safe="")
    return Response(
        contents, media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename=\"entrega.txt\"; filename*=UTF-8''{filename}"},
    )


@router.get("/submissions/{submission_id}/eliminar")
def confirm_submission_delete(request: Request, submission_id: UUID):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        submission = submission_metadata(conn, submission_id)
    return view(request, "submission-delete.html", user=user, submission=submission)


@router.post("/submissions/{submission_id}/eliminar")
def remove_submission(request: Request, submission_id: UUID, csrf: Csrf):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request, csrf)
        lock_submission_scopes(conn, [submission_id])
        submission_metadata(conn, submission_id)
        delete_submissions(conn, [submission_id])
    return redirect("/admin/submissions")


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
        grade = get_grade(conn, submission_id)
    return view(request, "submission.html", user=user, submission=submission, grade=grade,
                mail_ready=notices_enabled())


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
