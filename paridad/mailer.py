"""Shared SMTP transport. Credentials are supplied by the deployment."""

import os
import smtplib
import ssl
from email.message import EmailMessage


def smtp_configured() -> bool:
    return bool(os.getenv("PARIDAD_SMTP_HOST", "").strip())


def send_email(message: EmailMessage) -> None:
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
