"""Administrative reports, grouped similarities and file management."""

import re
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Query, Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from .admin import ContextQuery, Csrf, PAGE_SIZE, Page, SearchForm, SearchQuery, administrator, clean_student_search, student_match
from .bulk import consume_deletion, deletion_preview
from .file_archives import submission_bytes, submission_zip
from .grade_routes import get_grade
from .notices import publish_manual
from .report_excel import MAX_SHEET_ROWS, XLSX_MEDIA_TYPE, Percentage, report_excel
from .similarity_groups import similarity_groups
from .submission_records import delete_submissions, lock_submission_scopes
from .web import redirect, view, wants_json

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


SUBMISSION_FILTER = f"""
    WHERE (%s::bigint IS NULL OR a.id = %s) AND (%s::bigint IS NULL OR l.id = %s)
      AND (NOT %s OR s.max_similarity_bp > 100 * COALESCE(a.similarity_threshold, cfg.similarity_threshold)
          OR (s.ai_credit IS NULL AND s.ai_bp > 100 * COALESCE(a.ai_threshold, cfg.ai_threshold)))
      AND {student_match("u")}
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
PAIR_STUDENT_FILTER = f" AND ({student_match('ul')} OR {student_match('ur')})"


# Optional selection shared by the Excel preflight and its streamed worksheets.
EXPORT_PAIRS = PAIR_LIST + PAIR_STUDENT_FILTER + " AND (%s::uuid[] IS NULL OR p.left_id = ANY(%s) OR p.right_id = ANY(%s))"
EXPORT_SUBMISSIONS = SUBMISSION_LIST + SUBMISSION_FILTER + " AND (%s::uuid[] IS NULL OR s.id = ANY(%s))"
PAIR_ORDER = " ORDER BY p.score_bp DESC, p.left_id, p.right_id"
FRAGMENT_PAGE_LINES = 60
FRAGMENT_CONTEXT = 3
ReportOrigin = Annotated[Literal["informes", "archivos"], Query()]



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


def submission_page(conn, context, page: int, search: str, *, alerts_only: bool = False):
    return conn.execute(
        SUBMISSION_LIST + SUBMISSION_FILTER + " ORDER BY s.created_at DESC, s.id DESC LIMIT %s OFFSET %s",
        (*context, alerts_only, search, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
    ).fetchall()


def group_page(conn, filters, context, page: int, search: str):
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
    if search and groups:
        matching_ids = {
            row["id"] for row in conn.execute(
                f"""SELECT s.id FROM submissions s JOIN users u ON u.id = s.student_id
                    WHERE (%s::bigint IS NULL OR s.activity_id = %s)
                      AND (%s::bigint IS NULL OR s.language_id = %s) AND {student_match("u")}""",
                (*context, search),
            )
        }
        groups = [group for group in groups if any(item in matching_ids for item, _ in group.members)]
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
    alerts_only: bool = False, page: Page = 1, q: SearchQuery = "",
    vista: Literal["entregas", "comparaciones", "grupos"] = "entregas",
):
    q = clean_student_search(q)
    with request.app.state.pool.connection() as conn:
        # Keep the group graph and its loaded member records in one snapshot.
        if vista == "grupos":
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        user = administrator(conn, request)
        filters = filter_choices(conn, activity_id, language_id)
        context = context_params(filters)
        if vista == "grupos":
            rows, has_next = group_page(conn, filters, context, page, q)
        elif vista == "comparaciones":
            rows = conn.execute(
                PAIR_LIST + PAIR_STUDENT_FILTER + PAIR_ORDER + " LIMIT %s OFFSET %s",
                (*context, alerts_only, q, q, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
            ).fetchall()
            has_next = len(rows) > PAGE_SIZE
        else:
            rows = submission_page(conn, context, page, q, alerts_only=alerts_only)
            has_next = len(rows) > PAGE_SIZE
    return view(
        request, "reports.html", user=user, **filters, alerts_only=alerts_only, tab=vista,
        rows=rows[:PAGE_SIZE], page=page, q=q, has_next=has_next,
    )


def comparison_export_rows(conn, parameters, search, selection):
    with conn.cursor(name="report_excel_pairs") as cursor:
        cursor.itersize = 400
        cursor.execute(EXPORT_PAIRS + PAIR_ORDER + " LIMIT %s",
                       (*parameters, search, search, selection, selection, selection, MAX_SHEET_ROWS - 1))
        for item in cursor:
            yield (
                item["code"], item["language"], item["left_username"], item["left_name"], item["left_filename"],
                item["right_username"], item["right_name"], item["right_filename"],
                Percentage(item["score_bp"]), Percentage(item["similarity_threshold"] * 100),
                "Revisión" if item["alert"] else "Dentro del umbral",
            )


def submission_export_rows(conn, parameters, search, selection):
    with conn.cursor(name="report_excel_submissions") as cursor:
        cursor.itersize = 400
        cursor.execute(EXPORT_SUBMISSIONS + " ORDER BY s.created_at DESC, s.id DESC LIMIT %s",
                       (*parameters, search, selection, selection, MAX_SHEET_ROWS - 1))
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


def export_workbook(pool, parameters, search, selection):
    with pool.connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        sheets = (
            (
                "Comparaciones",
                ("Actividad", "Lenguaje", "Usuario A", "Estudiante A", "Archivo A", "Usuario B", "Estudiante B",
                 "Archivo B", "Similitud", "Umbral de similitud", "Estado"),
                comparison_export_rows(conn, parameters, search, selection),
            ),
            (
                "Entregas",
                ("Actividad", "Lenguaje", "Usuario", "Estudiante", "Archivo", "Estado",
                 "Similitud máxima", "Umbral de similitud", "Indicio de IA", "Umbral de IA", "IA declarada",
                 "Crédito de IA", "Revisión de IA", "Nota automática (0–50)", "Nota docente (0–50)", "Nota final (0–50)"),
                submission_export_rows(conn, parameters, search, selection),
            ),
        )
        yield from report_excel(sheets)


def report_download(request, activity_id, language_id, alerts_only, search, selection=None, csrf=None):
    pool = request.app.state.pool
    with pool.connection() as conn:
        administrator(conn, request, csrf)
        activity, language = optional_context_id(activity_id), optional_context_id(language_id)
        parameters = (activity, activity, language, language, alerts_only)
        for query, ids in (
            (EXPORT_PAIRS, (search, search, selection, selection, selection)),
            (EXPORT_SUBMISSIONS, (search, selection, selection)),
        ):
            count = conn.execute(
                f"SELECT count(*) AS total FROM ({query} LIMIT %s) AS report_rows",
                (*parameters, *ids, MAX_SHEET_ROWS),
            ).fetchone()["total"]
            if count >= MAX_SHEET_ROWS:
                raise HTTPException(413, "El informe supera el tamaño de una hoja de Excel. Aplica filtros.")
            if query == EXPORT_SUBMISSIONS and selection is not None and count != len(selection):
                raise HTTPException(409, "La selección cambió. Vuelve a seleccionar los archivos.")
    return StreamingResponse(
        export_workbook(pool, parameters, search, selection), media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="paridad-informes.xlsx"'},
    )


@router.get("/reports/exportar")
def export_reports(request: Request, activity_id: ContextQuery = "", language_id: ContextQuery = "",
                   alerts_only: bool = False, q: SearchQuery = ""):
    return report_download(request, activity_id, language_id, alerts_only, clean_student_search(q))


@router.post("/reports/exportar")
def export_selected_reports(
    request: Request, csrf: Csrf,
    selection: Annotated[list[UUID], Form()] = [], all_results: Annotated[bool, Form()] = False,
    activity_id: Annotated[str, Form(max_length=19)] = "",
    language_id: Annotated[str, Form(max_length=19)] = "", alerts_only: Annotated[bool, Form()] = False,
    q: SearchForm = "",
):
    chosen = sorted(set(selection)) if selection and not all_results else None
    return report_download(request, activity_id, language_id, alerts_only, clean_student_search(q), chosen, csrf)


@router.get("/submissions")
def submissions_list(
    request: Request, activity_id: ContextQuery = "", language_id: ContextQuery = "", page: Page = 1,
    q: SearchQuery = "",
):
    q = clean_student_search(q)
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        filters = filter_choices(conn, activity_id, language_id)
        submissions = submission_page(conn, context_params(filters), page, q)
    return view(
        request, "submissions.html", user=user, **filters, submissions=submissions[:PAGE_SIZE],
        page=page, has_next=len(submissions) > PAGE_SIZE, q=q,
    )


@router.post("/avisos")
def send_review_notices(
    request: Request, csrf: Csrf,
    selection: Annotated[list[UUID], Form()] = [], all_results: Annotated[bool, Form()] = False,
    activity_id: Annotated[str, Form(max_length=19)] = "",
    language_id: Annotated[str, Form(max_length=19)] = "", alerts_only: Annotated[bool, Form()] = False,
    destination: Annotated[Literal["reports", "submissions"], Form()] = "reports",
    preview: Annotated[bool, Form()] = False, q: SearchForm = "",
):
    q = clean_student_search(q)
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request, csrf)
        activity, language = optional_context_id(activity_id), optional_context_id(language_id)
        ids = None if all_results else sorted(set(selection))
        rows = conn.execute(
            """SELECT s.id, s.student_id FROM submissions s
               JOIN activities a ON a.id = s.activity_id JOIN languages l ON l.id = s.language_id
               JOIN users u ON u.id = s.student_id CROSS JOIN settings cfg""" + SUBMISSION_FILTER + " AND (%s::uuid[] IS NULL OR s.id = ANY(%s))",
            (activity, activity, language, language, alerts_only, q, ids, ids),
        ).fetchall()
        if not rows:
            raise HTTPException(400, "Selecciona al menos un archivo.")
        if ids is not None and len(rows) != len(ids):
            raise HTTPException(409, "La selección cambió. Vuelve a seleccionar los archivos.")
        students = sorted({row["student_id"] for row in rows})
        if preview:
            return view(request, "notices-confirm.html", user=user, total=len(students),
                        destination=destination, selection=ids or [], all_results=all_results,
                        activity_id=activity_id, language_id=language_id, alerts_only=alerts_only, q=q)
        published = publish_manual(conn, [row["id"] for row in rows])
    skipped = len(students) - published
    if wants_json(request):
        return JSONResponse({"published": published, "skipped": skipped})
    return view(request, "notices-result.html", user=user, published=published, skipped=skipped,
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
    q: SearchForm = "",
):
    pool = request.app.state.pool
    with pool.connection() as conn:
        user = administrator(conn, request, csrf)
        activity, language = optional_context_id(activity_id), optional_context_id(language_id)
        requested = None if all_results else sorted(set(selection))
        ids = [row["id"] for row in conn.execute(
            f"""SELECT s.id FROM submissions s JOIN users u ON u.id = s.student_id
                WHERE (%s::bigint IS NULL OR s.activity_id = %s) AND (%s::bigint IS NULL OR s.language_id = %s)
                  AND {student_match("u")} AND (%s::uuid[] IS NULL OR s.id = ANY(%s)) ORDER BY s.id""",
            (activity, activity, language, language, clean_student_search(q), requested, requested),
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
        deleted = delete_submissions(conn, ids)
    if wants_json(request):
        return JSONResponse({"deleted": deleted})
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
        deleted = delete_submissions(conn, [submission_id])
    if wants_json(request):
        return JSONResponse({"deleted": deleted})
    return redirect("/admin/submissions")


def fragment_window(source: str, span: list[int], page: int):
    lines = source.split("\n")
    start, end = span
    first = max(1, start - FRAGMENT_CONTEXT)
    last = min(len(lines), end + FRAGMENT_CONTEXT)
    offset = first + (page - 1) * FRAGMENT_PAGE_LINES
    shown = [
        (number, line, start <= number <= end)
        for number, line in enumerate(lines[offset - 1:min(last, offset + FRAGMENT_PAGE_LINES - 1)], offset)
    ]
    return shown, last >= offset + FRAGMENT_PAGE_LINES


@router.get("/reports/{left_id}/{right_id}")
def pair_report(
    request: Request, left_id: UUID, right_id: UUID,
    bloque: Annotated[int, Query(ge=1)] = 1, tramo: Annotated[int, Query(ge=1, le=3000)] = 1,
    desde: UUID | None = None, origen: ReportOrigin = "informes",
):
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
    fragments = pair["fragments"]
    fragment, left_lines, right_lines, has_next = None, [], [], False
    if fragments:
        if bloque > len(fragments):
            raise HTTPException(404, "Bloque no encontrado.")
        fragment = fragments[bloque - 1]
        left_lines, left_next = fragment_window(pair["left_source"], fragment["left"], tramo)
        right_lines, right_next = fragment_window(pair["right_source"], fragment["right"], tramo)
        if not left_lines and not right_lines:
            raise HTTPException(404, "Tramo no encontrado.")
        has_next = left_next or right_next
    elif bloque != 1 or tramo != 1:
        raise HTTPException(404, "Bloque no encontrado.")
    local_origin = desde if desde in (left_id, right_id) else None
    return view(request, "pair.html", user=user, pair=pair, fragment=fragment, block=bloque, segment=tramo,
                left_lines=left_lines, right_lines=right_lines, has_next=has_next, overview_id=local_origin, origin=origen)


@router.get("/submissions/{submission_id}")
def submission_report(
    request: Request, submission_id: UUID, origen: ReportOrigin = "archivos", comparaciones: Page = 1,
):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        submission = conn.execute(
            """SELECT s.id, s.filename, s.source, s.state, s.ai_bp, s.ai_credit, s.ai_features,
                      s.analysis_version, s.max_similarity_bp, s.activity_id, s.language_id, a.code, l.name AS language,
                      u.display_name, u.username, COALESCE(a.ai_threshold, cfg.ai_threshold) AS ai_threshold,
                      COALESCE(a.similarity_threshold, cfg.similarity_threshold) AS similarity_threshold
               FROM submissions s JOIN activities a ON a.id = s.activity_id JOIN languages l ON l.id = s.language_id
               JOIN users u ON u.id = s.student_id CROSS JOIN settings cfg WHERE s.id = %s""",
            (submission_id,),
        ).fetchone()
        if not submission:
            raise HTTPException(404, "Entrega no encontrada.")
        grade = get_grade(conn, submission_id)
        peers = conn.execute(
            PAIR_LIST + " AND (p.left_id = %s OR p.right_id = %s)" + PAIR_ORDER + " LIMIT %s OFFSET %s",
            (submission["activity_id"], submission["activity_id"], submission["language_id"],
             submission["language_id"], False, submission_id, submission_id, PAGE_SIZE + 1,
             (comparaciones - 1) * PAGE_SIZE),
        ).fetchall()
    return view(request, "submission.html", user=user, submission=submission, grade=grade, origin=origen,
                peers=peers[:PAGE_SIZE], has_next_pair=len(peers) > PAGE_SIZE, comparison_page=comparaciones)


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
