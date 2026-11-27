"""Publish review notices and serve a private inbox for each student."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from starlette.responses import JSONResponse

from .admin import Csrf, PAGE_SIZE, Page
from .security import auth_user, check_csrf
from .web import redirect, view, wants_json

router = APIRouter(prefix="/student/avisos")
InboxPage = Annotated[int, Form(ge=1, le=10000)]
OWN_NOTICES = """
    SELECT n.id, n.submission_id, n.similarity, n.ai, n.created_at, n.read_at,
           n.similarity_at_notice_bp, n.similarity_threshold_at_notice,
           n.ai_at_notice_bp, n.ai_threshold_at_notice,
           s.filename, a.code AS activity_code, a.title AS activity, l.name AS language,
           CASE WHEN n.submission_id IS NULL THEN (
               SELECT jsonb_agg(jsonb_build_object('filename', own.filename, 'activity', act.title,
                                                   'activity_code', act.code, 'language', lang.name)
                                ORDER BY own.created_at DESC, own.id)
               FROM submissions own
               JOIN activities act ON act.id = own.activity_id
               JOIN languages lang ON lang.id = own.language_id
               WHERE own.student_id = n.student_id AND own.id = ANY(n.related_submission_ids)
           ) END AS related_deliveries
    FROM review_notices n
    LEFT JOIN submissions s ON s.id = n.submission_id AND s.student_id = n.student_id
    LEFT JOIN activities a ON a.id = s.activity_id
    LEFT JOIN languages l ON l.id = s.language_id
    WHERE n.student_id = %s
"""


def publish_automatic(conn) -> None:
    conn.execute(
        """WITH eligible AS (
               SELECT s.id, s.student_id, s.max_similarity_bp, s.ai_bp,
                   COALESCE(a.similarity_threshold, cfg.similarity_threshold) AS similarity_threshold,
                   COALESCE(a.ai_threshold, cfg.ai_threshold) AS ai_threshold,
                   NOT s.noticed_similarity AND s.max_similarity_bp >
                       100 * COALESCE(a.similarity_threshold, cfg.similarity_threshold) AS similarity,
                   NOT s.noticed_ai AND s.ai_credit IS NULL AND s.ai_bp >
                       100 * COALESCE(a.ai_threshold, cfg.ai_threshold) AS ai
               FROM submissions s JOIN activities a ON a.id = s.activity_id
               JOIN users u ON u.id = s.student_id CROSS JOIN settings cfg
               WHERE s.state = 'completed' AND u.active
                 AND ((NOT s.noticed_similarity AND s.max_similarity_bp >
                       100 * COALESCE(a.similarity_threshold, cfg.similarity_threshold))
                   OR (NOT s.noticed_ai AND s.ai_credit IS NULL AND s.ai_bp >
                       100 * COALESCE(a.ai_threshold, cfg.ai_threshold)))
               ORDER BY s.created_at, s.id LIMIT 200 FOR NO KEY UPDATE OF s SKIP LOCKED
           ), published AS (
               INSERT INTO review_notices (
                   student_id, submission_id, similarity, ai,
                   similarity_at_notice_bp, similarity_threshold_at_notice, ai_at_notice_bp, ai_threshold_at_notice
               )
               SELECT student_id, id, similarity, ai,
                   CASE WHEN similarity THEN max_similarity_bp END,
                   CASE WHEN similarity THEN similarity_threshold END,
                   CASE WHEN ai THEN ai_bp END,
                   CASE WHEN ai THEN ai_threshold END
               FROM eligible
               RETURNING submission_id, similarity, ai
           )
           UPDATE submissions s SET noticed_similarity = s.noticed_similarity OR q.similarity,
               noticed_ai = s.noticed_ai OR q.ai FROM published q WHERE s.id = q.submission_id"""
    )


def publish_manual(conn, submission_ids: list[UUID]) -> int:
    # Serialize overlapping clicks across administrators while applying the cooldown.
    conn.execute("SELECT pg_advisory_xact_lock(602541918)")
    return conn.execute(
        """INSERT INTO review_notices (student_id, related_submission_ids)
           SELECT s.student_id, array_agg(s.id ORDER BY s.created_at DESC, s.id)
           FROM submissions s JOIN users u ON u.id = s.student_id
           WHERE s.id = ANY(%s) AND u.role = 'STUDENT' AND u.active AND NOT EXISTS (
                 SELECT 1 FROM review_notices n WHERE n.student_id = u.id AND n.submission_id IS NULL
                   AND n.created_at > now() - interval '1 minute'
             )
           GROUP BY s.student_id""",
        (submission_ids,),
    ).rowcount


@router.get("")
def inbox(request: Request, page: Page = 1):
    with request.app.state.pool.connection() as conn:
        user = auth_user(conn, request, "STUDENT")
        notices = conn.execute(
            OWN_NOTICES + " ORDER BY n.created_at DESC, n.id DESC LIMIT %s OFFSET %s",
            (user["id"], PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
        ).fetchall()
    return view(request, "student-notices.html", user=user, notices=notices[:PAGE_SIZE],
                page=page, has_next=len(notices) > PAGE_SIZE)


@router.post("/leer")
def read_notice(
    request: Request, csrf: Csrf, notice_id: Annotated[UUID, Form()],
    page: InboxPage = 1, abrir: Annotated[bool, Form()] = False,
):
    with request.app.state.pool.connection() as conn:
        user = auth_user(conn, request, "STUDENT")
        check_csrf(user["csrf"], csrf)
        updated = conn.execute(
            """UPDATE review_notices SET read_at = COALESCE(read_at, now())
               WHERE id = %s AND student_id = %s RETURNING id""",
            (notice_id, user["id"]),
        ).fetchone()
        if not updated:
            raise HTTPException(404, "Aviso no encontrado.")
        unread = conn.execute(
            "SELECT count(*) AS unread FROM review_notices WHERE student_id = %s AND read_at IS NULL",
            (user["id"],),
        ).fetchone()["unread"]
        if wants_json(request):
            return JSONResponse({"unread": unread})
        if abrir:
            notice = conn.execute(OWN_NOTICES + " AND n.id = %s", (user["id"], notice_id)).fetchone()
            user["unread_notices"] = unread
            return view(request, "student-notice.html", user=user, notice=notice, page=page)
    return redirect(f"/student/avisos?page={page}")
