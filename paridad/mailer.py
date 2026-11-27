"""SMTP submission through a relay or through Paridad’s private mail service."""

import os
import smtplib
import ssl
from email.message import EmailMessage


def smtp_configured() -> bool:
    return bool(os.getenv("PARIDAD_SMTP_HOST", "").strip())


def send_email(message: EmailMessage, *, direct: bool = False) -> None:
    if direct:
        # Only the worker and the MTA share this Docker network.
        with smtplib.SMTP("mail-out", 587, timeout=8) as smtp:
            smtp.send_message(message)
        return

    host = os.environ["PARIDAD_SMTP_HOST"].strip()
    port = int(os.getenv("PARIDAD_SMTP_PORT", "587") or "587")
    username = os.getenv("PARIDAD_SMTP_USERNAME", "")
    password = os.getenv("PARIDAD_SMTP_PASSWORD", "")
    if not 1 <= port <= 65535 or bool(username) != bool(password):
        raise ValueError("Configuración SMTP inválida")
    with smtplib.SMTP(host, port, timeout=8) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if username:
            smtp.login(username, password)
        smtp.send_message(message)
