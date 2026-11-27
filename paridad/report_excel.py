"""Write the two report sheets as a streaming, text-safe Excel workbook."""

from dataclasses import dataclass
from itertools import chain
import re
from xml.sax.saxutils import escape, quoteattr

from .file_archives import packed_zip

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_SHEET_ROWS = 1_048_576
SPREADSHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
BAD_XML_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


@dataclass(frozen=True)
class Percentage:
    basis_points: int


def cells(values):
    for value in values:
        if value is None:
            yield "<c/>"
        elif isinstance(value, Percentage):
            yield f'<c s="1"><v>{value.basis_points / 10000:.4f}</v></c>'
        elif isinstance(value, int):
            yield f"<c><v>{value}</v></c>"
        else:
            text = escape(BAD_XML_CHARACTERS.sub("�", str(value)))
            yield f'<c t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def sheet_xml(headers, rows):
    widths = "".join(
        f'<col min="{index}" max="{index}" width="{min(31, max(15, len(header) + 2))}" customWidth="1"/>'
        for index, header in enumerate(headers, start=1)
    )
    yield (
        f'<worksheet xmlns="{SPREADSHEET}"><sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        f'</sheetView></sheetViews><cols>{widths}</cols><sheetData>'
    ).encode()
    for number, row in enumerate(chain((headers,), rows), start=1):
        yield (f'<row r="{number}">' + "".join(cells(row)) + "</row>").encode()
    yield b"</sheetData></worksheet>"


def workbook_entries(sheets):
    sheet_refs = []
    links = []
    types = []
    for number, (title, _, _) in enumerate(sheets, start=1):
        sheet_refs.append(f'<sheet name={quoteattr(title)} sheetId="{number}" r:id="rId{number}"/>')
        links.append(f'<Relationship Id="rId{number}" Type="{OFFICE_RELS}/worksheet" Target="worksheets/sheet{number}.xml"/>')
        types.append(
            f'<Override PartName="/xl/worksheets/sheet{number}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    yield "[Content_Types].xml", (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + "".join(types) + "</Types>"
    ).encode()
    yield "_rels/.rels", (
        f'<Relationships xmlns="{PACKAGE_RELS}"><Relationship Id="rId1" Type="{OFFICE_RELS}/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    ).encode()
    yield "xl/workbook.xml", (
        f'<workbook xmlns="{SPREADSHEET}" xmlns:r="{OFFICE_RELS}">'
        '<bookViews><workbookView/></bookViews><sheets>'
        + "".join(sheet_refs) + "</sheets></workbook>"
    ).encode()
    yield "xl/_rels/workbook.xml.rels", (
        f'<Relationships xmlns="{PACKAGE_RELS}">' + "".join(links)
        + f'<Relationship Id="styles" Type="{OFFICE_RELS}/styles" Target="styles.xml"/></Relationships>'
    ).encode()
    yield "xl/styles.xml", (
        f'<styleSheet xmlns="{SPREADSHEET}">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    ).encode()
    for number, (_, headers, rows) in enumerate(sheets, start=1):
        yield f"xl/worksheets/sheet{number}.xml", sheet_xml(headers, rows)


def report_excel(sheets):
    return packed_zip(workbook_entries(sheets))
