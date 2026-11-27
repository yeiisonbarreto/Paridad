"""Read a bounded student roster and write Excel files with text-only cells."""

from dataclasses import dataclass
from io import BytesIO
import re
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from openpyxl import Workbook, load_workbook

from .config import MAX_FILE_BYTES
from .security import USERNAME, normalized_email, normalized_username

MAX_ROSTER_ROWS = 200
COLUMN_NAMES = {
    "id": "id", "ids": "id",
    "nombre": "nombre", "nombres": "nombre", "nombre completo": "nombre",
    "correo": "correo", "correo electrónico": "correo", "email": "correo", "e-mail": "correo",
}
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class RosterError(ValueError):
    pass


@dataclass(frozen=True)
class RosterStudent:
    line: int
    username: str
    name: str
    email: str


def check_archive(contents: bytes) -> None:
    if len(contents) > MAX_FILE_BYTES:
        raise RosterError("El archivo Excel supera los 128 KiB permitidos.")
    with ZipFile(BytesIO(contents)) as archive:
        entries = archive.infolist()
        if len(entries) > 128 or sum(item.file_size for item in entries) > 8 * 1024 * 1024:
            raise RosterError("El archivo Excel contiene demasiados datos. Guarda solo la lista de estudiantes.")
        if any(
            item.flag_bits & 1 or item.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
            or item.filename.lower().endswith("/vbaproject.bin")
            for item in entries
        ):
            raise RosterError("Usa un archivo .xlsx sin macros ni contraseña.")


def read_id(cell) -> str:
    value = cell.value
    if type(value) in (int, float) and (type(value) is int or value.is_integer()) and value >= 0:
        value = str(int(value))
        if len(value) > 15:
            raise RosterError("Guarda los IDs de más de 15 dígitos como texto en Excel.")
        if re.fullmatch(r"0{2,64}", cell.number_format or ""):
            value = value.zfill(len(cell.number_format))
    if not isinstance(value, str) or not USERNAME.fullmatch(normalized_username(value)):
        raise RosterError("El ID debe ser un usuario válido de hasta 64 caracteres.")
    return normalized_username(value)


def parse_roster(contents: bytes) -> list[RosterStudent]:
    try:
        check_archive(contents)
        workbook = load_workbook(BytesIO(contents), read_only=True, keep_links=False, data_only=False)
        try:
            return read_roster_sheet(workbook.worksheets[0])
        finally:
            workbook.close()
    except RosterError:
        raise
    except Exception as exc:
        raise RosterError("No se pudo leer el archivo. Usa un libro de Excel .xlsx válido.") from exc


def read_roster_sheet(sheet) -> list[RosterStudent]:
    # Infer rows from the worksheet XML instead of trusting the reported dimensions.
    sheet.reset_dimensions()
    rows = iter(sheet.iter_rows(min_row=1, max_col=3))
    header = next(rows, ())
    columns = {
        COLUMN_NAMES.get(str(cell.value).strip().casefold()): index
        for index, cell in enumerate(header)
    }
    if set(columns) != {"id", "nombre", "correo"}:
        raise RosterError("La primera fila debe tener las columnas ID, Nombre y Correo.")

    result = []
    usernames, emails = set(), set()
    for line, row in enumerate(rows, start=2):
        if line > MAX_ROSTER_ROWS + 1:
            raise RosterError(f"Usa un máximo de {MAX_ROSTER_ROWS} filas de estudiantes por archivo.")
        if all(cell.value is None or (isinstance(cell.value, str) and not cell.value.strip()) for cell in row):
            continue
        try:
            if any(cell.data_type in {"e", "f"} for cell in row):
                raise RosterError("Usa valores de texto o números, sin fórmulas ni errores de Excel.")
            username = read_id(row[columns["id"]])
            name = row[columns["nombre"]].value
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or any(
                ord(char) < 32 or ord(char) == 127 for char in name.strip()
            ):
                raise RosterError("Escribe un nombre válido de hasta 120 caracteres.")
            email_value = row[columns["correo"]].value
            if not isinstance(email_value, str):
                raise RosterError("Escribe un correo electrónico válido.")
            email = normalized_email(email_value)
            if username in usernames or email.lower() in emails:
                raise RosterError("El ID o correo está repetido en el archivo.")
        except ValueError as exc:
            raise RosterError(f"Fila {line}: {exc}") from exc
        result.append(RosterStudent(line, username, name.strip(), email))
        usernames.add(username)
        emails.add(email.lower())
    if not result:
        raise RosterError("Agrega al menos un estudiante en la primera hoja.")
    return result


def workbook_bytes(headers: tuple[str, ...], rows=()) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Estudiantes"
    sheet.freeze_panes = "A2"
    for line, values in enumerate((headers, *rows), start=1):
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=line, column=column, value=value)
            # Protect IDs and text that Excel could interpret as a number or formula.
            cell.data_type = "s"
            cell.number_format = "@"
    for letter, width in zip("ABCD", (22, 36, 42, 30)):
        sheet.column_dimensions[letter].width = width
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()
