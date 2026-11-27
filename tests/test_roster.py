from io import BytesIO
import unittest

from openpyxl import Workbook, load_workbook

from paridad.roster import MAX_ROSTER_ROWS, RosterError, parse_roster, workbook_bytes


class RosterTests(unittest.TestCase):
    def read_sheet(self, headers, rows):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        data = BytesIO()
        workbook.save(data)
        workbook.close()
        return parse_roster(data.getvalue())

    def test_ids_as_entered_and_headers_in_any_order(self):
        students = self.read_sheet(
            ["Correo electrónico", "IDS", "Nombres"], [
                ["ana@example.edu", "00042", "Ana María"],
                ["bruno@example.edu", 7, "Bruno"],
            ],
        )
        self.assertEqual([(s.username, s.name) for s in students], [("00042", "Ana María"), ("7", "Bruno")])

    def test_rejects_case_insensitive_duplicates(self):
        with self.assertRaisesRegex(RosterError, r"Fila 3"):
            self.read_sheet(
                ["ID", "Nombre", "Correo"],
                [["a1", "Ana", "ana@example.edu"], ["a2", "Ana", "ANA@example.edu"]],
            )

    def test_rejects_formulas_and_invalid_email(self):
        for name, email in [("=1+1", "ana@example.edu"), ("Ana", "incorrecto")]:
            with self.subTest(name=name, email=email), self.assertRaisesRegex(RosterError, r"Fila 2"):
                self.read_sheet(["ID", "Nombre", "Correo"], [["a1", name, email]])

    def test_rejects_too_many_rows(self):
        with self.assertRaisesRegex(RosterError, "máximo"):
            self.read_sheet(
                ["ID", "Nombre", "Correo"],
                [[str(n), f"Nombre {n}", f"n{n}@example.edu"] for n in range(MAX_ROSTER_ROWS + 1)],
            )

    def test_download_stores_ids_and_formula_like_names_as_text(self):
        data = workbook_bytes(
            ("ID", "Nombre", "Correo", "Contraseña inicial"),
            [("00042", "=Alumno", "ana@example.edu", "clave-inicial")],
        )
        workbook = load_workbook(BytesIO(data))
        try:
            self.assertEqual(workbook.active["A2"].value, "00042")
            self.assertEqual(workbook.active["B2"].value, "=Alumno")
            self.assertEqual(workbook.active["B2"].data_type, "s")
        finally:
            workbook.close()


if __name__ == "__main__":
    unittest.main()
