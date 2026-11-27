"""Verify the deployed app against an isolated Compose project."""

import getpass
import os
import re
import secrets
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import httpx
from openpyxl import Workbook, load_workbook

BASE = os.getenv("PARIDAD_TEST_URL", "http://localhost:8787").rstrip("/")
CSRF = re.compile(r'name="csrf" value="([^"]+)"')
PAIR_LINK = re.compile(r'href="(/admin/reports/[a-f0-9-]+/[a-f0-9-]+)"')
SUBMISSION_LINK = re.compile(r'href="(/admin/submissions/[a-f0-9-]+)(?:\?origen=informes)?"')
MOODLE_ORIGIN = os.getenv("PARIDAD_TEST_MOODLE_ORIGIN", "")
STYLE_CODE = """/* <b>marca de formato</b> */
int process_value(int initial_value) {
""" + """ // Step: check the current value before moving to the next example.
 if (initial_value > 0) initial_value--;
""" * 12 + """ return initial_value;
}
"""
RENAMED_CODE = STYLE_CODE.replace("process_value", "normalize_value").replace("initial_value", "sample_value")


def assert_status(response, status=200):
    assert response.status_code == status, (response.request.method, response.request.url, response.status_code)
    return response


def csrf(client, page):
    match = CSRF.search(assert_status(client.get(page)).text)
    assert match, page
    return match[1]


def login(client, username, password):
    response = assert_status(client.post("/login", data={
        "username": username, "password": password, "csrf": csrf(client, "/login"),
    }), 303)
    if BASE.startswith("https://"):
        session = next(item.lower() for item in response.headers.get_list("set-cookie")
                       if item.startswith("__Host-paridad="))
        assert "secure" in session and "httponly" in session
        assert f"samesite={'none' if MOODLE_ORIGIN else 'lax'}" in session
    return response.headers["location"]


def post(client, path, csrf_token, status=303, **data):
    return assert_status(client.post(path, data={"csrf": csrf_token, **data}), status)


def upload(client, token, activity, language, filename, content, *, request_id=None, credit="", status=303):
    fields = {"activity_id": activity, "language_id": language, "csrf": token,
              "request_id": request_id or str(uuid4())}
    if credit:
        fields.update({"ai_declared": "true", "ai_credit": credit})
    return assert_status(client.post(
        "/student/submissions", data=fields, files={"file": (filename, content, "text/plain")},
    ), status)


def history(client):
    rows = assert_status(client.get("/api/student/submissions")).json()
    keys = {"filename", "activity", "language", "submitted_at", "state", "similarity_bp",
            "similarity_alert", "ai_bp", "ai_alert", "ai_declared", "id", "grade", "clarification_status"}
    assert all(set(row) == keys for row in rows)
    return rows


def wait_result(client, filename, minimum_bp=0):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        row = next((value for value in history(client) if value["filename"] == filename), None)
        if row and row["state"] == "failed":
            raise AssertionError(f"Falló el análisis de {filename}")
        if row and row["state"] == "completed" and row["similarity_bp"] >= minimum_bp:
            return row
        time.sleep(0.4)
    raise AssertionError(f"Resultado pendiente para {filename}, mínimo {minimum_bp}")


def wait_notice(client, label):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        page = assert_status(client.get("/student/avisos")).text
        if label in page:
            return page
        time.sleep(0.4)
    raise AssertionError(f"No apareció el aviso esperado: {label}")


def main():
    username = os.getenv("PARIDAD_TEST_ADMIN", "docente")
    password = os.getenv("PARIDAD_TEST_PASSWORD") or getpass.getpass("Contraseña de la cuenta de prueba: ")
    student_pw = secrets.token_urlsafe(18)
    suffix = secrets.token_hex(4)
    first_name, second_name = f"ana_{suffix}", f"bruno_{suffix}"
    lab, separate_lab, concurrent_lab = f"lab_{suffix}", f"otro_{suffix}", f"par_{suffix}"
    language_code = f"texto_{suffix}"
    ca_file = os.getenv("PARIDAD_TEST_CA_FILE")
    verify = ssl.create_default_context(cafile=ca_file) if ca_file else True

    with ExitStack() as stack:
        def client():
            return stack.enter_context(httpx.Client(
                base_url=BASE, headers={"Origin": BASE}, timeout=15, verify=verify, trust_env=False,
            ))

        admin, first, second, guest = client(), client(), client(), client()
        assert_status(guest.get("/healthz"))
        assert_status(guest.get("/api/student/submissions"), 401)
        assert_status(guest.get("/admin/reports"), 303)
        assert login(admin, username, password) == "/admin"
        admin_csrf = csrf(admin, "/admin")
        assert_status(admin.get("/api/student/submissions"), 403)
        if BASE.startswith("https://"):
            assert "max-age=" in assert_status(admin.get("/admin")).headers["strict-transport-security"]
        assert "frame-ancestors 'none'" in assert_status(admin.get("/admin")).headers["content-security-policy"]
        assert "no-store" in assert_status(admin.get("/admin")).headers["cache-control"]

        post(admin, "/admin/accounts", admin_csrf, username=first_name, display_name=first_name, password=student_pw)
        roster = Workbook()
        roster.active.append(("ID", "Nombre", "Correo"))
        roster.active.append((second_name, second_name, f"{second_name}@example.edu"))
        xlsx = BytesIO()
        roster.save(xlsx)
        roster.close()
        imported = assert_status(admin.post("/admin/accounts/importar", data={"csrf": admin_csrf},
                                           files={"roster": ("estudiantes.xlsx", xlsx.getvalue())}))
        assert "paridad-credenciales.xlsx" in imported.headers["content-disposition"]
        credentials = load_workbook(BytesIO(imported.content), read_only=True)
        try:
            assert list(credentials.active.values)[0] == ("ID", "Contraseña")
            assert credentials.active.max_column == 2
            assert credentials.active["A2"].value == second_name
            second_pw = credentials.active["B2"].value
        finally:
            credentials.close()
        post(admin, "/admin/languages", admin_csrf, code=language_code, name=language_code,
             extensions=".txt", comments="c", keywords="int return if")
        module_code = f"mod_{suffix}"
        post(admin, "/admin/activities/modulos", admin_csrf, code=module_code, title="Módulo común")
        activities_page = assert_status(admin.get("/admin/activities/crear/actividad")).text
        module_id = re.search(r'<option value="(\d+)">' + module_code.upper() + r' ·', activities_page)[1]
        c_id = re.search(r'name="language_ids" value="([0-9]+)"\s*> C</label>', activities_page)[1]
        custom_id = re.search(r'name="language_ids" value="([0-9]+)"\s*> ' + language_code + "</label>", activities_page)[1]
        post(admin, "/admin/activities", admin_csrf, code=lab, title="Laboratorio común", language_ids=[c_id, custom_id],
             module_id=module_id)
        post(admin, "/admin/activities", admin_csrf, code=separate_lab, title="Laboratorio aparte", language_ids=c_id,
             module_id=module_id)
        post(admin, "/admin/activities", admin_csrf, code=concurrent_lab, title="Laboratorio concurrente", language_ids=c_id)
        date = datetime.now(ZoneInfo(os.getenv("PARIDAD_TIME_ZONE", "America/Bogota")))
        deadline_lab = f"hora_{suffix}"

        def local_date(days):
            return (date + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")

        post(admin, "/admin/activities", admin_csrf, 400, code=deadline_lab, title="Entrega programada", language_ids=c_id,
             module_id=module_id, opens_at=local_date(2), closes_at=local_date(1))
        post(admin, "/admin/activities", admin_csrf, code=deadline_lab, title="Entrega programada", language_ids=c_id,
             module_id=module_id, opens_at=local_date(1), closes_at=local_date(2))
        post(admin, "/admin/settings", admin_csrf, similarity_threshold=40, ai_threshold=30)

        assert login(first, first_name, student_pw) == "/student"
        assert login(second, second_name, second_pw) == "/student"
        first_csrf, second_csrf = csrf(first, "/student"), csrf(second, "/student")
        student_response = assert_status(first.get("/student"))
        expected_ancestors = MOODLE_ORIGIN or "'none'"
        assert f"frame-ancestors {expected_ancestors}" in student_response.headers["content-security-policy"]
        assert_status(first.get("/static/student.min.js"))
        assert_status(first.get("/static/paridad.css"))
        assert_status(first.get("/admin/accounts"), 403)
        post(first, "/admin/settings", first_csrf, 403, similarity_threshold=1, ai_threshold=1)
        post(admin, "/admin/settings", "token-inválido", 403, similarity_threshold=1, ai_threshold=1)
        assert_status(admin.post("/admin/settings", headers={"Origin": "https://invalid.example"},
                                 data={"csrf": admin_csrf, "similarity_threshold": 1, "ai_threshold": 1}), 403)

        def delivery_id(page, code):
            return re.search(r'data-delivery-id="(\d+)"[^>]*>\s*<div class="record-row">\s*'
                             r'<span class="record-summary-text">' + code.upper(), page)[1]

        module_page = assert_status(first.get("/student", params={"module_id": module_id})).text
        activity_id = delivery_id(module_page, lab)
        separate_id = delivery_id(module_page, separate_lab)
        deadline_id = delivery_id(module_page, deadline_lab)
        assert not any(concurrent_lab.upper() in option for option in re.findall(r'<option[^>]*>([^<]+)</option>', module_page))
        assert re.search(rf'<option value="{deadline_id}" disabled>', module_page)
        deadline_route = f"/admin/activities/{deadline_id}"
        assert_status(first.get(deadline_route + "/editar"), 403)
        upload(second, second_csrf, deadline_id, c_id, "temprano.c", STYLE_CODE, status=400)
        post(admin, deadline_route, admin_csrf, title="Vencida", language_ids=c_id, is_open="true",
             opens_at=local_date(-2), closes_at=local_date(-1))
        upload(second, second_csrf, deadline_id, c_id, "tarde.c", STYLE_CODE, status=400)
        post(admin, deadline_route + "/estado", admin_csrf, is_open="false")
        assert "Reabrir entregas" in assert_status(admin.get(deadline_route + "/estado")).text
        post(admin, deadline_route + "/eliminar", admin_csrf)
        assert_status(admin.get(deadline_route + "/editar"), 404)
        nonce = str(uuid4())
        upload(first, first_csrf, activity_id, c_id, "primero.c", STYLE_CODE, request_id=nonce)
        row = wait_result(first, "primero.c")
        assert row["similarity_bp"] == 0 and not row["similarity_alert"]
        original_grade = row["grade"]
        assert row["ai_bp"] > 3000 and row["ai_alert"]
        ai_notice = wait_notice(first, "primero.c")
        assert "indicio de IA" in ai_notice and "Umbral 30 %" in ai_notice
        assert "Buenas prácticas" not in ai_notice
        assert_status(admin.get("/student/avisos"), 403)
        upload(first, first_csrf, activity_id, c_id, "primero.c", STYLE_CODE, request_id=nonce)
        upload(first, first_csrf, activity_id, c_id, "mismo-estudiante.c", STYLE_CODE)
        assert wait_result(first, "mismo-estudiante.c")["similarity_bp"] == 0
        assert len(history(first)) == 2
        assert history(second) == []

        with ThreadPoolExecutor(max_workers=2) as executor:
            tasks = [
                executor.submit(upload, first, first_csrf, activity_id, custom_id, "otro-lenguaje.txt", STYLE_CODE),
                executor.submit(upload, second, second_csrf, separate_id, c_id, "otra-actividad.c", RENAMED_CODE),
            ]
            for task in tasks:
                task.result()
        assert wait_result(first, "otro-lenguaje.txt")["similarity_bp"] == 0
        assert wait_result(second, "otra-actividad.c")["similarity_bp"] == 0
        assert wait_result(first, "primero.c")["similarity_bp"] == 0

        upload(second, second_csrf, activity_id, c_id, "compania.c", RENAMED_CODE, credit="Herramienta de ejemplo")
        second_row = wait_result(second, "compania.c", 10000)
        assert second_row["similarity_alert"] and second_row["ai_declared"] and not second_row["ai_alert"]
        assert wait_result(first, "primero.c", 10000)["similarity_alert"]
        own_page = assert_status(first.get("/student")).text
        assert "100,00 %" in own_page and second_name not in own_page and "compania.c" not in own_page
        assert [row["filename"] for row in history(second)] == ["compania.c", "otra-actividad.c"]

        scope_filters = {"activity_id": activity_id, "language_id": c_id}
        reports = assert_status(admin.get("/admin/reports", params=scope_filters)).text
        assert 'id="entregas"' in reports and 'id="comparaciones"' not in reports and 'id="grupos"' not in reports
        assert 'href="/admin/aclaraciones"' in reports
        assert "/descargar" not in reports and "/eliminar" not in reports
        comparisons = assert_status(admin.get("/admin/reports", params=dict(scope_filters, vista="comparaciones"))).text
        assert 'id="comparaciones"' in comparisons and 'id="entregas"' not in comparisons
        pair_links = PAIR_LINK.findall(comparisons)
        assert len(pair_links) == 2 and first_name in comparisons and second_name in comparisons
        search_params = dict(scope_filters, q=second_name.upper())
        filtered_report = assert_status(admin.get("/admin/reports", params=search_params)).text
        assert "compania.c" in filtered_report and "primero.c" not in filtered_report
        filtered_comparisons = assert_status(admin.get(
            "/admin/reports", params=dict(search_params, vista="comparaciones"),
        )).text
        assert len(PAIR_LINK.findall(filtered_comparisons)) == 2
        groups_report = assert_status(admin.get("/admin/reports", params=dict(search_params, vista="grupos"))).text
        assert 'id="grupos"' in groups_report and 'id="entregas"' not in groups_report
        assert "3 archivos" in groups_report and "primero.c" in groups_report and "compania.c" in groups_report
        filtered_files = assert_status(admin.get("/admin/submissions", params=search_params)).text
        assert "compania.c" in filtered_files and "primero.c" not in filtered_files
        filtered_export = post(admin, "/admin/reports/exportar", admin_csrf, 200, all_results="true", **search_params)
        filtered_workbook = load_workbook(BytesIO(filtered_export.content))
        try:
            assert filtered_workbook["Entregas"].max_row == 2
            assert filtered_workbook["Entregas"]["E2"].value == "compania.c"
            assert filtered_workbook["Comparaciones"].max_row == 3
        finally:
            filtered_workbook.close()
        archive = post(admin, "/admin/submissions/lote", admin_csrf, 200, action="zip", all_results="true", **search_params)
        with ZipFile(BytesIO(archive.content)) as bundle:
            assert len(bundle.namelist()) == 1 and "compania.c" in bundle.namelist()[0]
        notice_preview = post(admin, "/admin/avisos", admin_csrf, 200, preview="true", all_results="true", **search_params)
        assert "bandeja de 1 estudiante" in notice_preview.text
        detail = assert_status(admin.get(pair_links[0])).text
        assert "Fragmentos coincidentes" in detail and "líneas " in detail and "100,00 %" in detail
        assert detail.count('class="comparison-code"') == 2 and "code-row code-match" in detail
        assert_status(admin.get(pair_links[0], params={"bloque": 1000}), 404)
        assert_status(first.get(pair_links[0]), 403)
        for link in set(SUBMISSION_LINK.findall(reports)):
            detail = assert_status(admin.get(link)).text
            assert "Comentarios explicativos" in detail and "Ajustar nota" in detail
            assert "Comparaciones de esta entrega" in detail
            assert "/descargar" not in detail and "/eliminar" not in detail and "file-menu" not in detail
            assert "<b>marca de formato</b>" not in detail and "&lt;b&gt;marca de formato&lt;/b&gt;" in detail
            assert_status(second.get(link), 403)
            download = assert_status(admin.get(link + "/descargar"))
            assert download.content in {STYLE_CODE.encode(), RENAMED_CODE.encode()}
            assert download.headers["content-disposition"].startswith("attachment;")
            assert_status(first.get(link + "/descargar"), 403)
        assert_status(admin.get("/admin/reports?activity_id=&language_id="))

        filters = {"activity_id": activity_id, "language_id": c_id, "alerts_only": "true"}
        assert_status(first.get("/admin/reports/exportar", params=filters), 403)
        export = assert_status(admin.get("/admin/reports/exportar", params=filters))
        assert 'filename="paridad-informes.xlsx"' in export.headers["content-disposition"]
        workbook = load_workbook(BytesIO(export.content))
        try:
            assert workbook.sheetnames == ["Comparaciones", "Entregas"]
            pairs_sheet, submissions_sheet = workbook.worksheets
            assert pairs_sheet["I2"].value == 1 and pairs_sheet["I2"].number_format == "0.00%"
            assert pairs_sheet["J2"].value == 0.4 and pairs_sheet.max_row == 3
            assert all(row[0] == lab and row[1] == "C" for row in submissions_sheet.iter_rows(min_row=2, values_only=True))
            assert {row[4] for row in submissions_sheet.iter_rows(min_row=2, values_only=True)} == {
                "primero.c", "mismo-estudiante.c", "compania.c",
            }
            assert submissions_sheet["I2"].number_format == "0.00%"
        finally:
            workbook.close()

        graded = wait_result(first, "primero.c", 10000)
        assert graded["grade"] == max(0, original_grade - 30)
        inbox_first = wait_notice(first, "La similitud")
        inbox_second = wait_notice(second, "compania.c")
        assert "compania.c" not in inbox_first and "primero.c" not in inbox_second
        assert second_name not in inbox_first and first_name not in inbox_second
        assert "100,00 %" in inbox_first and "Umbral 40 %" in inbox_first
        assert "Umbral 40 %" in inbox_second and "Qué puede hacer" in inbox_second
        post(admin, "/admin/settings", admin_csrf, similarity_threshold=70, ai_threshold=60)
        after_change = assert_status(first.get("/student/avisos")).text
        assert "Umbral 40 %" in after_change and "Umbral 30 %" in after_change
        post(admin, "/admin/settings", admin_csrf, similarity_threshold=40, ai_threshold=30)
        notice_id = re.search(r'name="notice_id" value="([^"]+)"', inbox_first)[1]
        read_path = "/student/avisos/leer"
        post(second, read_path, second_csrf, 404, notice_id=notice_id)
        post(first, read_path, "token-inválido", 403, notice_id=notice_id)
        reading = first.post(read_path, data={"csrf": first_csrf, "notice_id": notice_id},
                             headers={"Accept": "application/json"})
        unread = assert_status(reading).json()["unread"]
        assert notice_id in assert_status(first.get("/student/avisos")).text
        reading_again = first.post(read_path, data={"csrf": first_csrf, "notice_id": notice_id},
                                   headers={"Accept": "application/json"})
        assert assert_status(reading_again).json()["unread"] == unread

        selection = [graded["id"], second_row["id"],
                     wait_result(first, "mismo-estudiante.c")["id"]]
        post(first, "/admin/avisos", first_csrf, 403, selection=selection)
        preview = post(admin, "/admin/avisos", admin_csrf, 200, selection=selection, preview="true")
        assert "Confirmar envío" in preview.text
        assert "Revisa tus entregas" not in assert_status(first.get("/student/avisos")).text
        sent = admin.post("/admin/avisos", data={
            "csrf": admin_csrf, "selection": selection, "activity_id": activity_id, "language_id": c_id,
        }, headers={"Accept": "application/json"})
        assert assert_status(sent).json() == {"published": 2, "skipped": 0}
        repeated = post(admin, "/admin/avisos", admin_csrf, 200, selection=selection)
        assert "0 avisos publicados" in repeated.text
        assert "Revisa tus entregas" in assert_status(first.get("/student/avisos")).text
        assert "Revisa tus entregas" in assert_status(second.get("/student/avisos")).text
        assert_status(first.post(read_path, data={"csrf": first_csrf}), 422)
        private_ids = re.findall(r'name="notice_id" value="([^"]+)"', assert_status(first.get("/student/avisos")).text)
        opened = post(first, read_path, first_csrf, 200, notice_id=private_ids[0], abrir="true")
        assert "Paridad le solicita" in opened.text
        assert "Entregas por revisar" in opened.text
        assert "primero.c" in opened.text and "mismo-estudiante.c" in opened.text
        assert "compania.c" not in opened.text and second_name not in opened.text
        assert "mismo-estudiante.c" not in assert_status(second.get("/student/avisos")).text
        for private_id in private_ids[1:]:
            post(first, read_path, first_csrf, notice_id=private_id)
        read_inbox = assert_status(first.get("/student/avisos")).text
        assert 'class="notice-count" hidden' in read_inbox and "Revisa tus entregas" in read_inbox
        assert 'class="notice-count" hidden' not in assert_status(second.get("/student")).text

        selected = post(admin, "/admin/reports/exportar", admin_csrf, 200, selection=graded["id"], **filters)
        workbook = load_workbook(BytesIO(selected.content))
        try:
            assert [row[4] for row in workbook["Entregas"].iter_rows(min_row=2, values_only=True)] == ["primero.c"]
            pairs_sheet = workbook["Comparaciones"]
            assert pairs_sheet.max_row == 2
            assert {pairs_sheet["E2"].value, pairs_sheet["H2"].value} == {"primero.c", "compania.c"}
        finally:
            workbook.close()
        post(first, "/admin/reports/exportar", first_csrf, 403, selection=graded["id"], **filters)
        post(admin, "/admin/reports/exportar", admin_csrf, 409, selection=graded["id"],
             activity_id=separate_id, language_id=c_id)
        assert 0 <= graded["grade"] <= 50
        grade_url = f"/student/submissions/{graded['id']}/nota"
        grade_html = assert_status(first.get(grade_url)).text
        assert "Comentarios" in grade_html
        assert grade_html.count("<textarea") == 1 and 'name="reason"' in grade_html
        assert_status(second.get(grade_url), 404)
        pending_url = "/api/admin/aclaraciones/pendientes"
        assert_status(first.get(pending_url), 403)
        pending_before = assert_status(admin.get(pending_url)).json()["pending"]
        clarification = f"/student/submissions/{graded['id']}/aclaracion"
        requested = first.post(clarification, data={
            "csrf": first_csrf, "reason": "Verificar los comentarios", "comments": "Revisar este archivo.",
        }, headers={"Accept": "application/json"})
        receipt = assert_status(requested).json()
        assert "Solicitud recibida" in receipt["content"] and receipt["unread"] == 1
        post(first, clarification, first_csrf, 409, reason="Duplicada")
        assert assert_status(admin.get(pending_url)).json()["pending"] == pending_before + 1
        assert "primero.c" in assert_status(admin.get("/admin/aclaraciones")).text
        assert "primero.c" not in assert_status(admin.get("/admin/aclaraciones", params={"q": second_name})).text
        assert "primero.c" in assert_status(admin.get("/admin/aclaraciones", params={"q": first_name.upper()})).text
        thread = re.compile(r'data-refresh-thread(?P<unread> data-unread)?[^>]*>.*?name="notice_id" value="([^"]+)"', re.S)
        student_inbox = assert_status(first.get("/student/avisos")).text
        entry = thread.search(student_inbox)
        assert entry["unread"] and "Paridad" in student_inbox
        thread_id = entry[2]
        post(second, read_path, second_csrf, 404, notice_id=thread_id)

        opened = first.post(read_path, data={
            "csrf": first_csrf, "notice_id": thread_id, "abrir": "true",
        }, headers={"Accept": "application/json"})
        pending_thread = assert_status(opened).json()
        assert "Solicitud recibida" in pending_thread["content"]
        assert "Resolución de Paridad" not in pending_thread["content"] and pending_thread["unread"] == 0

        decision = f"/admin/submissions/{graded['id']}/aclaracion"
        post(first, decision, first_csrf, 403, decision="resolved", response="Sin acceso")
        sent_decision = post(admin, decision, admin_csrf, decision="resolved", response="Comentario del docente", grade=42)
        assert sent_decision.headers["location"].endswith("?respuesta=enviada#aclaracion")
        assert wait_result(first, "primero.c")["grade"] == 42
        assert "Comentario del docente" in assert_status(first.get(grade_url)).text
        assert "primero.c" not in assert_status(admin.get("/admin/aclaraciones")).text
        assert assert_status(admin.get(pending_url)).json()["pending"] == pending_before

        entries = list(thread.finditer(assert_status(first.get("/student/avisos")).text))
        assert len(entries) == 1 and entries[0][2] == thread_id and entries[0]["unread"]
        response = first.post(read_path, data={
            "csrf": first_csrf, "notice_id": thread_id, "abrir": "true",
        }, headers={"Accept": "application/json"})
        history_thread = assert_status(response).json()
        assert history_thread["activity_tick"] > pending_thread["activity_tick"]
        assert "Solicitud recibida" in history_thread["content"] and "Resolución de Paridad" in history_thread["content"]
        assert "Comentario del docente" in history_thread["content"] and "42 / 50" in history_thread["content"]
        assert "compania.c" not in history_thread["content"] and second_name not in history_thread["content"]

        second_request = f"/student/submissions/{second_row['id']}/aclaracion"
        post(second, second_request, second_csrf, reason="Solicito otra revisión.")
        second_decision = f"/admin/submissions/{second_row['id']}/aclaracion"
        post(admin, second_decision, admin_csrf, decision="rejected", response="Se ratifica la evaluación.")
        second_inbox = assert_status(second.get("/student/avisos")).text
        assert "Solicitud rechazada" in second_inbox and "Se ratifica la evaluación." in second_inbox
        assert "Comentario del docente" not in second_inbox and first_name not in second_inbox

        # Free two slots after the tests above, so the same student can test another activity.
        existing = {row["filename"]: row["id"] for row in history(first)}
        upload(first, first_csrf, separate_id, c_id, "cuarto.c", STYLE_CODE, status=409)
        for filename in ("otro-lenguaje.txt", "mismo-estudiante.c"):
            post(admin, f"/admin/submissions/{existing[filename]}/eliminar", admin_csrf)
        concurrent_module = re.search(r'/student\?module_id=(\d+)"[^>]*>\s*' + concurrent_lab.upper(),
                                      student_response.text)[1]
        concurrent_id = delivery_id(assert_status(first.get("/student", params={"module_id": concurrent_module})).text,
                                    concurrent_lab)
        with ThreadPoolExecutor(max_workers=2) as executor:
            tasks = [
                executor.submit(upload, first, first_csrf, concurrent_id, c_id, "par-uno.c", STYLE_CODE),
                executor.submit(upload, second, second_csrf, concurrent_id, c_id, "par-dos.c", RENAMED_CODE),
            ]
            for task in tasks:
                task.result()
        assert wait_result(first, "par-uno.c", 10000)["similarity_alert"]
        assert wait_result(second, "par-dos.c", 10000)["similarity_alert"]
        concurrent_report = admin.get("/admin/reports", params={"activity_id": concurrent_id, "language_id": c_id, "vista": "comparaciones"})
        assert len(PAIR_LINK.findall(assert_status(concurrent_report).text)) == 1

        bulk_files = "/admin/submissions/lote"
        scope = {"activity_id": concurrent_id, "language_id": c_id}
        post(first, bulk_files, first_csrf, 403, action="zip", all_results="true", **scope)
        archive = post(admin, bulk_files, admin_csrf, 200, action="zip", all_results="true", **scope)
        assert 'filename="paridad-archivos.zip"' in archive.headers["content-disposition"]
        with ZipFile(BytesIO(archive.content)) as files:
            assert {name.split("/")[-1]: files.read(name) for name in files.namelist()} == {
                "par-uno.c": STYLE_CODE.encode(), "par-dos.c": RENAMED_CODE.encode(),
            }
        archive = post(admin, bulk_files, admin_csrf, 200, action="zip", all_results="true",
                       activity_id=separate_id, language_id=c_id)
        with ZipFile(BytesIO(archive.content)) as files:
            assert [name.split("/")[-1] for name in files.namelist()] == ["otra-actividad.c"]
        preview = post(admin, bulk_files, admin_csrf, 200, action="delete", all_results="true", **scope)
        token = re.search(r'name="token" value="([^"]+)"', preview.text)[1]
        assert "2 archivos" in preview.text
        upload(first, first_csrf, concurrent_id, c_id, "después.c", STYLE_CODE)
        assert wait_result(first, "después.c", 10000)["similarity_alert"]
        post(admin, bulk_files + "/eliminar", admin_csrf, token=token)
        post(admin, bulk_files + "/eliminar", admin_csrf, 409, token=token)
        assert not any(row["filename"] == "par-uno.c" for row in history(first))
        assert wait_result(first, "después.c")["similarity_bp"] == 0

        post(admin, "/admin/settings", admin_csrf, similarity_threshold=100, ai_threshold=30)
        assert not wait_result(first, "primero.c", 10000)["similarity_alert"]
        post(admin, f"/admin/activities/{activity_id}", admin_csrf, language_ids=[c_id, custom_id],
             title="Laboratorio común", is_open="true", similarity_threshold=40)
        assert wait_result(first, "primero.c", 10000)["similarity_alert"]
        post(admin, "/admin/settings", admin_csrf, similarity_threshold=40, ai_threshold=30)

        upload(first, first_csrf, activity_id, c_id, "otro.exe", STYLE_CODE, status=400)
        upload(first, first_csrf, separate_id, custom_id, "deshabilitado.txt", STYLE_CODE, status=400)
        upload(first, first_csrf, activity_id, c_id, "binario.c", b"\x00\xff", status=400)
        upload(first, first_csrf, activity_id, c_id, "grande.c", b"x" * (128 * 1024 + 1), status=413)
        upload(first, first_csrf, activity_id, c_id, "largo.c", b"x" * (145 * 1024), status=413)

        account_page = assert_status(admin.get("/admin/accounts")).text
        def account_id_for(page, name):
            return re.search(r'<input type="checkbox" name="selection" value="(\d+)"[^>]*\('
                             + re.escape(name) + r'\)"', page)[1]

        account_id = account_id_for(account_page, second_name)
        assert "Modificar cuenta" in assert_status(admin.get(f"/admin/accounts/{account_id}/editar")).text
        post(admin, f"/admin/accounts/{account_id}/estado", admin_csrf, active="false")
        assert_status(second.get("/api/student/submissions"), 401)
        post(admin, f"/admin/accounts/{account_id}/estado", admin_csrf, active="true")
        post(admin, f"/admin/accounts/{account_id}", admin_csrf, display_name=second_name,
             active="true", new_password=secrets.token_urlsafe(18))
        assert_status(second.get("/api/student/submissions"), 401)

        delete_path = f"/admin/accounts/{account_id}/eliminar"
        assert_status(first.get(delete_path), 403)
        assert_status(admin.get(delete_path))
        post(admin, delete_path, admin_csrf, 400, confirmation="id-incorrecto")
        post(admin, delete_path, admin_csrf, confirmation=second_name)
        assert_status(admin.get(delete_path), 404)
        assert_status(admin.get(pair_links[0]), 404)
        assert all(row["similarity_bp"] == 0 for row in history(first))

        third_name = f"carla_{suffix}"
        post(admin, "/admin/accounts", admin_csrf, username=third_name, display_name=third_name, password=student_pw)
        account_page = assert_status(admin.get("/admin/accounts")).text
        ids = [account_id_for(account_page, name) for name in (first_name, third_name)]
        invitation = post(admin, f"/admin/accounts/{ids[1]}/invitacion", admin_csrf, 200)
        token = re.search(r'/invitacion\?token=([A-Za-z0-9_-]+)', invitation.text)[1]
        assert_status(guest.get("/invitacion", params={"token": token}))
        new_password = secrets.token_urlsafe(18)
        assert_status(guest.post("/invitacion", data={
            "token": token, "password": new_password, "confirmation": new_password,
        }), 303)
        assert_status(guest.get("/invitacion", params={"token": token}), 400)
        assert login(guest, third_name, new_password) == "/student"

        bulk_accounts = "/admin/accounts/lote"
        post(first, bulk_accounts, first_csrf, 403, action="disable", selection=ids)
        filtered_accounts = assert_status(admin.get("/admin/accounts", params={"q": third_name.upper()})).text
        assert third_name in filtered_accounts and first_name not in filtered_accounts
        assert "Sin cuentas para esta búsqueda." in assert_status(admin.get("/admin/accounts", params={"q": "%"})).text
        post(admin, bulk_accounts, admin_csrf, action="disable", all_results="true", q=third_name)
        assert_status(guest.get("/api/student/submissions"), 401)
        assert_status(first.get("/api/student/submissions"))
        post(admin, bulk_accounts, admin_csrf, action="disable", selection=ids)
        assert_status(first.get("/api/student/submissions"), 401)
        assert assert_status(admin.get("/admin/accounts")).text.count("Inhabilitada") >= 2
        post(admin, bulk_accounts, admin_csrf, action="enable", selection=ids)
        assert login(first, first_name, student_pw) == "/student"
        preview = post(admin, bulk_accounts, admin_csrf, 200, action="delete", selection=ids)
        assert "2 cuentas" in preview.text and "data-record-panel" in preview.text
        token = re.search(r'name="token" value="([^"]+)"', preview.text)[1]
        post(admin, bulk_accounts + "/eliminar", admin_csrf, token=token)
        assert_status(first.get("/api/student/submissions"), 401)
        for student_id in ids:
            assert_status(admin.get(f"/admin/accounts/{student_id}/eliminar"), 404)

    print("Flujo HTTP verificado: autorización, recálculo, ámbitos, fragmentos, crédito, límites, sesiones, manejo de cuentas, exportación, operaciones en lote, notas, aclaraciones y avisos internos.")


if __name__ == "__main__":
    main()
