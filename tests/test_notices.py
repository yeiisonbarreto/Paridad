import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from paridad.notices import notice_message


class ReviewNoticeTest(unittest.TestCase):
    @patch.dict("os.environ", {"PARIDAD_NOTICE_FROM": "notificaciones@example.edu"})
    def test_multimedia_notice_keeps_text_and_embeds_the_logo(self):
        job = {
            "id": uuid4(), "email": "alumno@example.edu", "display_name": "<Estudiante>",
            "submission_id": uuid4(), "filename": "entrega.c", "code": "lab01", "similarity": True, "ai": False,
        }
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7ioAAAAASUVORK5CYII="
        )
        with tempfile.TemporaryDirectory() as directory:
            logo = Path(directory, "paridad-logo.png")
            logo.write_bytes(png)
            with patch("paridad.notices.NOTICE_LOGO", logo):
                message = notice_message(job)
        self.assertEqual(message["From"], "Paridad <notificaciones@example.edu>")
        self.assertEqual(message["To"], "alumno@example.edu")
        self.assertEqual(message["Auto-Submitted"], "auto-generated")
        plain = message.get_body(("plain",)).get_content()
        self.assertIn("la similitud de código", plain)
        self.assertIn("No se admiten respuestas", plain)
        html = message.get_body(("html",)).get_content()
        self.assertIn("Hola, &lt;Estudiante&gt;", html)
        logo_part = next(part for part in message.walk() if part.get_content_type() == "image/png")
        self.assertIn(f'cid:{str(logo_part["Content-ID"])[1:-1]}', html)
        self.assertEqual(logo_part.get_content(), png)

    @patch.dict("os.environ", {"PARIDAD_NOTICE_FROM": "notificaciones@example.edu"})
    def test_manual_notice_without_logo_keeps_the_paridad_brand(self):
        job = {"id": uuid4(), "email": "alumno@example.edu", "display_name": "Estudiante", "submission_id": None}
        with tempfile.TemporaryDirectory() as directory:
            with patch("paridad.notices.NOTICE_LOGO", Path(directory, "missing.png")):
                message = notice_message(job)
        html = message.get_body(("html",)).get_content()
        self.assertIn(">Paridad</p>", html)
        self.assertIn("No se admiten respuestas", html)
        self.assertNotIn("cid:", html)
