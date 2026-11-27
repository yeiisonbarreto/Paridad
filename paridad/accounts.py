"""Administrator management of student accounts and roster imports."""

import secrets
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from psycopg import errors
from starlette.responses import Response

from .admin import Csrf, Name, PAGE_SIZE, Page, administrator, required_text
from .bulk import consume_deletion, deletion_preview
from .config import MAX_FILE_BYTES, PUBLIC_ORIGIN
from .invitations import create_invitation
from .roster import MAX_ROSTER_ROWS, XLSX_MEDIA_TYPE, RosterError, parse_roster, workbook_bytes
from .security import PASSWORDS, USERNAME, check_rate, normalized_email, normalized_username, valid_password
from .submission_records import delete_submissions, lock_submission_scopes
from .web import redirect, view

router = APIRouter(prefix="/admin/accounts")
Email = Annotated[str, Form(max_length=254)]
ACCOUNT_CONFLICT = "Ese usuario o correo ya pertenece a una cuenta."
ROSTER_HEADERS = ("ID", "Nombre", "Correo")


def optional_email(value: str) -> str | None:
    if not value.strip():
        return None
    try:
        return normalized_email(value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def student_account(conn, student_id: int, *, lock: bool = False):
    if not 0 < student_id < 2**63:
        raise HTTPException(404, "Cuenta no encontrada.")
    query = """SELECT id, username, display_name, email, active FROM users WHERE id = %s AND role = 'STUDENT'"""
    if lock:
        query += " FOR NO KEY UPDATE"
    student = conn.execute(query, (student_id,)).fetchone()
    if not student:
        raise HTTPException(404, "Cuenta no encontrada.")
    return student


def excel_file(contents: bytes, filename: str) -> Response:
    return Response(contents, media_type=XLSX_MEDIA_TYPE,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("")
def accounts(request: Request, page: Page = 1):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        students = conn.execute(
            """SELECT id, username, display_name, email, active FROM users WHERE role = 'STUDENT'
               ORDER BY username LIMIT %s OFFSET %s""",
            (PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
        ).fetchall()
    return view(
        request, "accounts.html", user=user, students=students[:PAGE_SIZE],
        page=page, has_next=len(students) > PAGE_SIZE, roster_limit=MAX_ROSTER_ROWS,
    )


@router.get("/plantilla")
def roster_template(request: Request):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request)
    return excel_file(workbook_bytes(ROSTER_HEADERS), "paridad-plantilla-estudiantes.xlsx")


@router.post("/importar")
def import_accounts(request: Request, csrf: Csrf, roster: Annotated[UploadFile, File()]):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request, csrf)
        if not (roster.filename or "").lower().endswith(".xlsx"):
            raise HTTPException(400, "Selecciona un archivo .xlsx.")
        contents = roster.file.read(MAX_FILE_BYTES + 1)
        if len(contents) > MAX_FILE_BYTES:
            raise HTTPException(413, "El archivo Excel supera los 128 KiB permitidos.")
        try:
            students = parse_roster(contents)
        except RosterError as exc:
            raise HTTPException(400, str(exc)) from None

        existing = conn.execute(
            """SELECT username, lower(email) AS email FROM users
               WHERE username = ANY(%s) OR lower(email) = ANY(%s)""",
            ([student.username for student in students], [student.email.lower() for student in students]),
        ).fetchall()
        usernames = {account["username"] for account in existing}
        emails = {account["email"] for account in existing}
        conflict = next(
            (student for student in students if student.username in usernames or student.email.lower() in emails),
            None,
        )
        if conflict:
            raise HTTPException(409, f"Fila {conflict.line}: {ACCOUNT_CONFLICT}")

        credentials = [(s.username, s.name, s.email, secrets.token_urlsafe(18)) for s in students]
        contents = workbook_bytes((*ROSTER_HEADERS, "Contraseña inicial"), credentials)
        hashes = [(username, name, email, PASSWORDS.hash(password)) for username, name, email, password in credentials]
        try:
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO users (username, display_name, email, password_hash, role)
                       VALUES (%s, %s, %s, %s, 'STUDENT')""",
                    hashes,
                )
        except errors.UniqueViolation:
            raise HTTPException(409, ACCOUNT_CONFLICT) from None
    return excel_file(contents, "paridad-credenciales.xlsx")


@router.post("")
def create_account(
    request: Request, csrf: Csrf, username: Annotated[str, Form(max_length=64)],
    display_name: Name, password: Annotated[str, Form(max_length=128)], email: Email = "",
):
    try:
        with request.app.state.pool.connection() as conn:
            administrator(conn, request, csrf)
            username = normalized_username(username)
            if not USERNAME.fullmatch(username) or not valid_password(password):
                raise HTTPException(400, "El usuario o la contraseña no cumple el formato. Usa al menos 12 caracteres de contraseña.")
            conn.execute(
                """INSERT INTO users (username, display_name, email, password_hash, role)
                   VALUES (%s, %s, %s, %s, 'STUDENT')""",
                (username, required_text(display_name), optional_email(email), PASSWORDS.hash(password)),
            )
    except errors.UniqueViolation:
        raise HTTPException(409, ACCOUNT_CONFLICT) from None
    return redirect("/admin/accounts")


def selected_students(conn, ids: list[int] | None, *, lock: bool = False) -> list[int]:
    query = "SELECT id FROM users WHERE role = 'STUDENT' AND (%s::bigint[] IS NULL OR id = ANY(%s)) ORDER BY id"
    if lock:
        query += " FOR NO KEY UPDATE"
    return [row["id"] for row in conn.execute(query, (ids, ids))]


def remove_student_accounts(conn, ids: list[int]) -> None:
    # The caller has locked the selected students, so their uploads cannot change.
    submissions = [row["id"] for row in conn.execute(
        "SELECT id FROM submissions WHERE student_id = ANY(%s)", (ids,),
    )]
    if submissions:
        lock_submission_scopes(conn, submissions)
        delete_submissions(conn, submissions)
    conn.execute("DELETE FROM users WHERE role = 'STUDENT' AND id = ANY(%s)", (ids,))


@router.post("/lote")
def bulk_accounts(
    request: Request, csrf: Csrf, action: Annotated[Literal["disable", "enable", "delete"], Form()],
    selection: Annotated[list[int], Form()] = [], all_results: Annotated[bool, Form()] = False,
):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request, csrf)
        requested = None if all_results else sorted(set(selection))
        if requested is not None and any(not 0 < item < 2**63 for item in requested):
            raise HTTPException(400, "Selección inválida.")
        ids = selected_students(conn, requested, lock=action != "delete")
        if requested is not None and requested != ids:
            raise HTTPException(409, "La selección cambió. Vuelve a seleccionar las cuentas.")
        if not ids:
            raise HTTPException(400, "Selecciona al menos una cuenta.")
        if action == "delete":
            return deletion_preview(conn, request, user, "accounts", ids)
        active = action == "enable"
        conn.execute("UPDATE users SET active = %s WHERE role = 'STUDENT' AND id = ANY(%s)", (active, ids))
        if not active:
            conn.execute("DELETE FROM sessions WHERE user_id = ANY(%s)", (ids,))
            conn.execute("DELETE FROM student_invitations WHERE user_id = ANY(%s)", (ids,))
    return redirect("/admin/accounts")


@router.post("/lote/eliminar")
def confirm_bulk_account_deletion(request: Request, csrf: Csrf, token: Annotated[UUID, Form()]):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request, csrf)
        selection = [int(item) for item in consume_deletion(conn, user["id"], "accounts", token)]
        ids = selected_students(conn, selection, lock=True)
        if ids:
            remove_student_accounts(conn, ids)
    return redirect("/admin/accounts")


@router.get("/{student_id}/editar")
@router.get("/{student_id}/estado")
def account_action(request: Request, student_id: int):
    operation = request.url.path.rsplit("/", 1)[-1]
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        student = student_account(conn, student_id)
    return view(request, "account-action.html", user=user, student=student, operation=operation)


@router.post("/{student_id}/estado")
def set_account_state(request: Request, student_id: int, csrf: Csrf, active: Annotated[bool, Form()]):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request, csrf)
        student_account(conn, student_id, lock=True)
        conn.execute("UPDATE users SET active = %s WHERE id = %s", (active, student_id))
        if not active:
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (student_id,))
            conn.execute("DELETE FROM student_invitations WHERE user_id = %s", (student_id,))
    return redirect("/admin/accounts")


@router.post("/{student_id}")
def update_account(
    request: Request, student_id: int, csrf: Csrf, display_name: Name,
    active: Annotated[bool, Form()] = False,
    new_password: Annotated[str, Form(max_length=128)] = "",
    email: Annotated[str | None, Form(max_length=254)] = None,
    edit_email: Annotated[bool, Form()] = False,
    preserve_active: Annotated[bool, Form()] = False,
):
    try:
        with request.app.state.pool.connection() as conn:
            administrator(conn, request, csrf)
            student = student_account(conn, student_id, lock=True)
            if preserve_active:
                active = student["active"]
            address = optional_email(email or "") if email is not None or edit_email else student["email"]
            if new_password and not valid_password(new_password):
                raise HTTPException(400, "La contraseña debe tener al menos 12 caracteres.")
            next_hash = PASSWORDS.hash(new_password) if new_password else None
            conn.execute(
                """UPDATE users SET display_name = %s, email = %s, active = %s,
                                    password_hash = COALESCE(%s, password_hash)
                   WHERE id = %s AND role = 'STUDENT'""",
                (required_text(display_name), address, active, next_hash, student_id),
            )
            if not active or next_hash:
                conn.execute("DELETE FROM sessions WHERE user_id = %s", (student_id,))
            if not active or next_hash or address != student["email"]:
                conn.execute("DELETE FROM student_invitations WHERE user_id = %s", (student_id,))
    except errors.UniqueViolation:
        raise HTTPException(409, ACCOUNT_CONFLICT) from None
    return redirect("/admin/accounts")


@router.post("/{student_id}/invitacion")
def invite_student(request: Request, student_id: int, csrf: Csrf):
    pool = request.app.state.pool
    with pool.connection() as conn:
        administrator(conn, request, csrf)
        student_account(conn, student_id)
    check_rate(pool, "send-invite", str(student_id), 5, 3600)

    with pool.connection() as conn:
        user = administrator(conn, request, csrf)
        student = student_account(conn, student_id, lock=True)
        if not student["active"]:
            raise HTTPException(409, "La cuenta debe estar activa para generar una invitación.")
        token = create_invitation(conn, student_id)
    return view(request, "invitation-link.html", user=user, student=student,
                invitation=f"{PUBLIC_ORIGIN}/invitacion?token={token}")


@router.get("/{student_id}/eliminar")
def confirm_delete(request: Request, student_id: int):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        student = student_account(conn, student_id)
        count = conn.execute(
            "SELECT count(*) AS total FROM submissions WHERE student_id = %s", (student_id,),
        ).fetchone()["total"]
    return view(request, "account-delete.html", user=user, student=student, submission_count=count)


@router.post("/{student_id}/eliminar")
def delete_account(
    request: Request, student_id: int, csrf: Csrf,
    confirmation: Annotated[str, Form(max_length=64)],
):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request, csrf)
        student = student_account(conn, student_id, lock=True)
        if confirmation.strip() != student["username"]:
            raise HTTPException(400, "Escribe el ID del estudiante para confirmar la eliminación.")
        remove_student_accounts(conn, [student_id])
    return redirect("/admin/accounts")
