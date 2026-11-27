import unittest
from email.message import EmailMessage
from unittest.mock import patch

from paridad.mailer import send_email
from paridad.notices import notices_enabled


class MailDeliveryTest(unittest.TestCase):
    @patch.dict("os.environ", {
        "PARIDAD_NOTICE_DELIVERY": "direct",
        "PARIDAD_NOTICE_FROM": "notificaciones@avisos.example.edu",
        "PARIDAD_MAIL_DOMAIN": "avisos.example.edu",
        "PARIDAD_SMTP_HOST": "",
    })
    def test_direct_mode_uses_the_domain_and_works_without_a_relay(self):
        self.assertTrue(notices_enabled())
        with patch.dict("os.environ", {"PARIDAD_NOTICE_FROM": "notificaciones@example.edu"}):
            self.assertFalse(notices_enabled())

    @patch.dict("os.environ", {"PARIDAD_SMTP_HOST": "unused.example.edu", "PARIDAD_SMTP_USERNAME": "unused"})
    @patch("paridad.mailer.smtplib.SMTP")
    def test_direct_mode_submits_only_to_the_private_service(self, smtp_client):
        message = EmailMessage()
        send_email(message, direct=True)
        smtp_client.assert_called_once_with("mail-out", 587, timeout=8)
        connection = smtp_client.return_value.__enter__.return_value
        connection.send_message.assert_called_once_with(message)
        connection.starttls.assert_not_called()
        connection.login.assert_not_called()

    @patch.dict("os.environ", {
        "PARIDAD_SMTP_HOST": "smtp.example.edu",
        "PARIDAD_SMTP_PORT": "587",
        "PARIDAD_SMTP_USERNAME": "relay",
        "PARIDAD_SMTP_PASSWORD": "test-password",
    })
    @patch("paridad.mailer.smtplib.SMTP")
    def test_relay_mode_preserves_tls_and_credentials(self, smtp_client):
        message = EmailMessage()
        send_email(message)
        smtp_client.assert_called_once_with("smtp.example.edu", 587, timeout=8)
        connection = smtp_client.return_value.__enter__.return_value
        self.assertEqual([call[0] for call in connection.method_calls], ["starttls", "login", "send_message"])
        connection.login.assert_called_once_with("relay", "test-password")
