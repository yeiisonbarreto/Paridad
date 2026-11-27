"""Student-owned grade details and administrator review of clarifications."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request

from .admin import Csrf, PAGE_SIZE, Page, administrator
from .security import auth_user, check_csrf
from .web import redirect, view

router = APIRouter()

GRADE_SELECT = """
    SELECT s.id, s.filename, a.code AS activity, s.state, s.rubric, s.automatic_grade,
           s.quality_points, s.grade_override, s.grade_note, c.reason, c.comments, c.grade_at_request,
           c.status AS clarification_status, c.response, c.created_at, c.resolved_at
    FROM submissions s JOIN activities a ON a.id = s.activity_id
    LEFT JOIN grade_clarifications c ON c.submission_id = s.id
"""
Grade = Annotated[int | None, Form(ge=0, le=50)]
Feedback = Annotated[str, Form(max_length=1500)]


def get_grade(conn, submission_id, student_id=None, lock=False):
    scope = " AND s.student_id = %s" if student_id is not None else ""
    params = (submission_id, student_id) if student_id is not None else (submission_id,)
    row = conn.execute(
        GRADE_SELECT + " WHERE s.id = %s" + scope + (" FOR NO KEY UPDATE OF s" if lock else ""),
        params,
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Entrega no encontrada.")
    return row


def comment_text(value, *, required=False):
    text = value.strip()
    if "\x00" in text or (required and not text):
        raise HTTPException(400, "Escribe un comentario válido.")
    return text or None


@router.get("/student/submissions/{submission_id}/nota")
def student_grade(request: Request, submission_id: UUID):
    with request.app.state.pool.connection() as conn:
        user = auth_user(conn, request, "STUDENT")
        grade = get_grade(conn, submission_id, student_id=user["id"])
    return view(request, "student-grade.html", user=user, grade=grade)


@router.post("/student/submissions/{submission_id}/aclaracion")
def request_clarification(
    request: Request, submission_id: UUID, csrf: Csrf, reason: Annotated[str, Form(max_length=600)],
    comments: Annotated[str, Form(max_length=800)] = "",
):
    with request.app.state.pool.connection() as conn:
        user = auth_user(conn, request, "STUDENT")
        check_csrf(user["csrf"], csrf)
        grade = get_grade(conn, submission_id, student_id=user["id"], lock=True)
        current = grade["grade_override"] if grade["grade_override"] is not None else grade["automatic_grade"]
        if current is None:
            raise HTTPException(409, "La entrega todavía no tiene nota.")
        row = conn.execute(
            """INSERT INTO grade_clarifications (submission_id, grade_at_request, reason, comments) VALUES (%s, %s, %s, %s)
               ON CONFLICT (submission_id) DO NOTHING RETURNING submission_id""",
            (submission_id, current, comment_text(reason, required=True), comment_text(comments)),
        ).fetchone()
        if not row:
            raise HTTPException(409, "Ya solicitaste una aclaración para este archivo.")
    return redirect(f"/student/submissions/{submission_id}/nota")


@router.get("/admin/aclaraciones")
def clarification_list(request: Request, page: Page = 1, state: Literal["pending", "all"] = "pending"):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        requests = conn.execute(
            """SELECT s.id, s.filename, COALESCE(s.grade_override, s.automatic_grade) AS grade,
                      a.code AS activity, u.display_name, u.username, c.status, c.created_at
               FROM grade_clarifications c JOIN submissions s ON c.submission_id = s.id
               JOIN activities a ON a.id = s.activity_id JOIN users u ON u.id = s.student_id
               WHERE (%s OR c.status = 'pending')
               ORDER BY c.created_at DESC, s.id LIMIT %s OFFSET %s""",
            (state == "all", PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
        ).fetchall()
    return view(request, "clarifications.html", user=user, rows=requests[:PAGE_SIZE],
                page=page, has_next=len(requests) > PAGE_SIZE, filter_state=state)


@router.post("/admin/submissions/{submission_id}/aclaracion")
def decide_clarification(
    request: Request, submission_id: UUID, csrf: Csrf,
    decision: Annotated[Literal["resolved", "rejected"], Form()], response: Feedback,
    grade: Grade = None,
):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request, csrf)
        get_grade(conn, submission_id, lock=True)
        explanation = comment_text(response, required=True)
        clarified = conn.execute(
            """UPDATE grade_clarifications SET status = %s, response = %s, resolved_at = now()
               WHERE submission_id = %s AND status = 'pending' RETURNING submission_id""",
            (decision, explanation, submission_id),
        ).fetchone()
        if not clarified:
            raise HTTPException(409, "La solicitud ya fue atendida o no existe.")
        if grade is not None:
            conn.execute("UPDATE submissions SET grade_override = %s, grade_note = %s WHERE id = %s",
                         (grade, explanation, submission_id))
    return redirect(f"/admin/submissions/{submission_id}#aclaracion")


@router.post("/admin/submissions/{submission_id}/nota")
def set_grade(request: Request, submission_id: UUID, csrf: Csrf, note: Feedback, grade: Grade = None):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request, csrf)
        current = get_grade(conn, submission_id, lock=True)
        if current["state"] != "completed":
            raise HTTPException(409, "Espera a que se complete el análisis para ajustar la nota.")
        conn.execute("UPDATE submissions SET grade_override = %s, grade_note = %s WHERE id = %s",
                     (grade, comment_text(note, required=True), submission_id))
    return redirect(f"/admin/submissions/{submission_id}#nota")
