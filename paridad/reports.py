"""Administrative reports, grouped similarities and file management."""

import re
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response

from .admin import ContextQuery, Csrf, PAGE_SIZE, Page, administrator
from .similarity_groups import similarity_groups
from .submission_records import delete_submissions
from .web import redirect, view

router = APIRouter(prefix="/admin")

SUBMISSION_LIST = """
    SELECT s.id, s.filename, s.state, s.ai_bp, s.ai_credit IS NOT NULL AS declared,
           a.code, l.name AS language, u.display_name, u.username,
           s.ai_credit IS NULL AND s.ai_bp > 100 * COALESCE(a.ai_threshold, cfg.ai_threshold) AS alert
    FROM submissions s JOIN activities a ON a.id = s.activity_id JOIN languages l ON l.id = s.language_id
    JOIN users u ON u.id = s.student_id CROSS JOIN settings cfg
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
        SUBMISSION_LIST + """
           WHERE (%s::bigint IS NULL OR a.id = %s) AND (%s::bigint IS NULL OR l.id = %s)
             AND (NOT %s OR (s.ai_credit IS NULL AND s.ai_bp > 100 * COALESCE(a.ai_threshold, cfg.ai_threshold)))
           ORDER BY s.created_at DESC, s.id DESC LIMIT %s OFFSET %s""",
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
            (*context, alerts_only, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
        ).fetchall()
        submissions = submission_page(conn, context, page, alerts_only=alerts_only)
    return view(
        request, "reports.html", user=user, **filters, alerts_only=alerts_only, groups=groups,
        pairs=pairs[:PAGE_SIZE], submissions=submissions[:PAGE_SIZE], page=page,
        has_next=groups_next or len(pairs) > PAGE_SIZE or len(submissions) > PAGE_SIZE,
    )


@router.get("/submissions")
def submissions_list(request: Request, activity_id: ContextQuery = "", language_id: ContextQuery = "", page: Page = 1):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        filters = filter_choices(conn, activity_id, language_id)
        submissions = submission_page(conn, context_params(filters), page)
    return view(
        request, "submissions.html", user=user, **filters, submissions=submissions[:PAGE_SIZE],
        page=page, has_next=len(submissions) > PAGE_SIZE,
    )


def submission_metadata(conn, submission_id: UUID):
    submission = conn.execute(
        """SELECT s.id, s.filename, s.activity_id, s.language_id, u.display_name, u.username
           FROM submissions s JOIN users u ON u.id = s.student_id WHERE s.id = %s""",
        (submission_id,),
    ).fetchone()
    if not submission:
        raise HTTPException(404, "Entrega no encontrada.")
    return submission


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
    contents = submission["source_bytes"]
    if contents is None:
        contents = submission["source"].encode("utf-8")
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
        submission = submission_metadata(conn, submission_id)
        # Match the scope lock of uploads and the worker before deleting results.
        conn.execute(
            """SELECT activity_id FROM activity_languages WHERE activity_id = %s AND language_id = %s
               FOR NO KEY UPDATE""",
            (submission["activity_id"], submission["language_id"]),
        ).fetchone()
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
