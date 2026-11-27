"""Administrative module and delivery management."""

from typing import Annotated, Literal

from fastapi import APIRouter, Form, HTTPException, Query, Request
from psycopg import errors

from .admin import Code, Csrf, Name, administrator, code_value, optional_threshold, required_text
from .analysis import COMMENT_STYLES
from .config import DISPLAY_TZ
from .delivery import database_time, delivery_status, parse_window
from .submission_records import delete_submissions
from .web import redirect, view

router = APIRouter(prefix="/admin/activities")
LIST = "/admin/activities"
WindowValue = Annotated[str, Form(max_length=16)]
ModuleId = Annotated[int | None, Form(gt=0, lt=2**63)]
ThresholdValue = Annotated[str, Form(max_length=3)]
LanguageIds = Annotated[list[int], Form()]
ACTIVITY_SELECT = """
    SELECT a.*, ARRAY(SELECT language_id FROM activity_languages WHERE activity_id = a.id) AS language_ids
    FROM activities a
"""


def activity_record(conn, activity_id: int, *, lock: bool = False):
    row = None
    if 0 < activity_id < 2**63:
        row = conn.execute(
            ACTIVITY_SELECT + " WHERE a.id = %s" + (" FOR UPDATE OF a" if lock else ""), (activity_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Actividad no encontrada.")
    return row


def module_record(conn, module_id: int, *, lock: bool = False):
    row = None
    if 0 < module_id < 2**63:
        row = conn.execute(
            "SELECT * FROM modules WHERE id = %s" + (" FOR UPDATE" if lock else ""), (module_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Módulo no encontrado.")
    return row


def form_options(conn):
    return {
        "modules": conn.execute("SELECT * FROM modules ORDER BY code").fetchall(),
        "languages": conn.execute("SELECT * FROM languages ORDER BY name").fetchall(),
        "time_zone": DISPLAY_TZ.key,
    }


@router.get("")
def activities(request: Request):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        modules = conn.execute("SELECT * FROM modules ORDER BY code").fetchall()
    return view(request, "activities.html", user=user, modules=modules)


@router.get("/modulos/{module_id}")
def module_detail(request: Request, module_id: int):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        module = module_record(conn, module_id)
        deliveries = conn.execute(
            "SELECT * FROM activities WHERE module_id = %s ORDER BY code", (module_id,),
        ).fetchall()
        now = database_time(conn)
        for delivery in deliveries:
            delivery["status"] = delivery_status(delivery, now)
    return view(request, "module-detail.html", user=user, module=module, deliveries=deliveries)


@router.get("/crear/{kind}")
def creation_form(
    request: Request, kind: Literal["modulo", "actividad", "lenguaje"],
    module_id: Annotated[int | None, Query(gt=0, lt=2**63)] = None,
):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        options = form_options(conn) if kind == "actividad" else {}
        module = module_record(conn, module_id) if kind == "actividad" and module_id is not None else None
    return view(
        request, "activity-create.html", user=user, kind=kind, module=module, **options,
        comment_styles={key: label for key, label in {
            "none": "Sin comentarios", "c": "// y /* … */", "marie": "/", "arm": "//, @, ; y /* … */",
            "vhdl": "--", "hash": "#", "semicolon": ";",
        }.items() if key in COMMENT_STYLES},
    )


@router.post("/modulos")
def create_module(request: Request, csrf: Csrf, code: Code, title: Name):
    try:
        with request.app.state.pool.connection() as conn:
            administrator(conn, request, csrf)
            conn.execute("INSERT INTO modules (code, title) VALUES (%s, %s)", (code_value(code), required_text(title)))
    except errors.UniqueViolation:
        raise HTTPException(409, "Ya existe un módulo con ese código.") from None
    return redirect(LIST)


@router.get("/modulos/{module_id}/{operation}")
def module_action(request: Request, module_id: int, operation: Literal["editar", "eliminar"]):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        module = module_record(conn, module_id)
        has_deliveries = conn.execute("SELECT 1 FROM activities WHERE module_id = %s LIMIT 1", (module_id,)).fetchone()
    return view(request, "module-action.html", user=user, module=module, operation=operation,
                has_deliveries=has_deliveries)


@router.post("/modulos/{module_id}/editar")
def update_module(request: Request, module_id: int, csrf: Csrf, code: Code, title: Name):
    try:
        with request.app.state.pool.connection() as conn:
            administrator(conn, request, csrf)
            module_record(conn, module_id, lock=True)
            conn.execute("UPDATE modules SET code = %s, title = %s WHERE id = %s",
                         (code_value(code), required_text(title), module_id))
    except errors.UniqueViolation:
        raise HTTPException(409, "Ya existe un módulo con ese código.") from None
    return redirect(LIST)


@router.post("/modulos/{module_id}/eliminar")
def delete_module(request: Request, module_id: int, csrf: Csrf):
    try:
        with request.app.state.pool.connection() as conn:
            administrator(conn, request, csrf)
            module_record(conn, module_id, lock=True)
            conn.execute("DELETE FROM modules WHERE id = %s", (module_id,))
    except errors.ForeignKeyViolation:
        raise HTTPException(409, "El módulo aún contiene actividades. Muévelas o elimínalas antes de continuar.") from None
    return redirect(LIST)


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


@router.post("")
def create_activity(
    request: Request, csrf: Csrf, code: Code, title: Name, language_ids: LanguageIds,
    module_id: ModuleId = None, opens_at: WindowValue = "", closes_at: WindowValue = "",
):
    try:
        with request.app.state.pool.connection() as conn:
            administrator(conn, request, csrf)
            code = code_value(code)
            title = required_text(title)
            start, end = parse_window(opens_at, closes_at)
            if module_id is None:
                # Older clients submitting a standalone activity use a module with the same code.
                conn.execute(
                    "INSERT INTO modules (code, title) VALUES (%s, %s) ON CONFLICT (code) DO NOTHING", (code, title),
                )
                module_id = conn.execute("SELECT id FROM modules WHERE code = %s", (code,)).fetchone()["id"]
            else:
                module_record(conn, module_id)
            row = conn.execute(
                """INSERT INTO activities (module_id, code, title, opens_at, closes_at)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (module_id, code, title, start, end),
            ).fetchone()
            update_languages(conn, row["id"], language_ids)
    except errors.UniqueViolation:
        raise HTTPException(409, "Ya existe una actividad con ese código.") from None
    except errors.ForeignKeyViolation:
        raise HTTPException(409, "El módulo cambió. Selecciónalo de nuevo.") from None
    return redirect(f"{LIST}/modulos/{module_id}")


@router.get("/{activity_id}/{operation}")
def activity_action(
    request: Request, activity_id: int, operation: Literal["editar", "eliminar", "estado"],
):
    with request.app.state.pool.connection() as conn:
        user = administrator(conn, request)
        activity = activity_record(conn, activity_id)
        context = {}
        if operation == "editar":
            context = form_options(conn)
        elif operation == "eliminar":
            context["submission_count"] = conn.execute("SELECT count(*) AS total FROM submissions WHERE activity_id = %s",
                                                       (activity_id,)).fetchone()["total"]
    return view(request, "activity-action.html", user=user, activity=activity, operation=operation, **context)


@router.post("/{activity_id}")
def update_activity(
    request: Request, activity_id: int, csrf: Csrf, title: Name, language_ids: LanguageIds,
    is_open: Annotated[bool, Form()] = False, similarity_threshold: ThresholdValue = "", ai_threshold: ThresholdValue = "",
    module_id: ModuleId = None, code: Code = "", opens_at: WindowValue = "", closes_at: WindowValue = "",
    edit_window: Annotated[bool, Form()] = False,
):
    try:
        with request.app.state.pool.connection() as conn:
            administrator(conn, request, csrf)
            activity = activity_record(conn, activity_id, lock=True)
            start, end = (parse_window(opens_at, closes_at) if edit_window or opens_at or closes_at
                          else (activity["opens_at"], activity["closes_at"]))
            if module_id is not None:
                module_record(conn, module_id)
            conn.execute(
                """UPDATE activities SET module_id = %s, code = %s, title = %s, is_open = %s,
                       similarity_threshold = %s, ai_threshold = %s, opens_at = %s, closes_at = %s WHERE id = %s""",
                (module_id or activity["module_id"], code_value(code) if code else activity["code"],
                 required_text(title), is_open, optional_threshold(similarity_threshold),
                 optional_threshold(ai_threshold), start, end, activity_id),
            )
            update_languages(conn, activity_id, language_ids)
    except errors.UniqueViolation:
        raise HTTPException(409, "Ya existe una actividad con ese código.") from None
    except errors.ForeignKeyViolation as exc:
        if exc.diag.constraint_name == "activities_module_id_fkey":
            raise HTTPException(409, "El módulo cambió. Selecciónalo de nuevo.") from None
        raise HTTPException(409, "Ese lenguaje ya tiene entregas en la actividad y debe conservarse.") from None
    return redirect(f"{LIST}/modulos/{module_id or activity['module_id']}")


@router.post("/{activity_id}/estado")
def set_activity_state(request: Request, activity_id: int, csrf: Csrf, is_open: Annotated[bool, Form()]):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request, csrf)
        activity = activity_record(conn, activity_id, lock=True)
        conn.execute("UPDATE activities SET is_open = %s WHERE id = %s", (is_open, activity_id))
    return redirect(f"{LIST}/modulos/{activity['module_id']}")


@router.post("/{activity_id}/eliminar")
def delete_activity(request: Request, activity_id: int, csrf: Csrf):
    with request.app.state.pool.connection() as conn:
        administrator(conn, request, csrf)
        activity = activity_record(conn, activity_id, lock=True)
        conn.execute(
            "SELECT language_id FROM activity_languages WHERE activity_id = %s ORDER BY language_id FOR UPDATE",
            (activity_id,),
        ).fetchall()
        ids = [row["id"] for row in conn.execute("SELECT id FROM submissions WHERE activity_id = %s", (activity_id,))]
        delete_submissions(conn, ids)
        conn.execute("DELETE FROM activity_languages WHERE activity_id = %s", (activity_id,))
        conn.execute("DELETE FROM activities WHERE id = %s", (activity_id,))
    return redirect(f"{LIST}/modulos/{activity['module_id']}")
