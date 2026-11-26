"""Runtime configuration shared by the web app and the worker."""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
MAX_FILE_BYTES = 128 * 1024
MAX_REQUEST_BYTES = 144 * 1024
MAX_TOKENS = 8000
MAX_SCOPE_SUBMISSIONS = int(os.getenv("PARIDAD_SCOPE_LIMIT", "250"))
if MAX_SCOPE_SUBMISSIONS < 1:
    raise ValueError("PARIDAD_SCOPE_LIMIT debe ser mayor que cero")
SESSION_HOURS = 8
LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def configured_origin(value: str, *, https_only: bool = True) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Puerto inválido en el origen configurado") from exc
    host = parsed.hostname or ""
    if (
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
        or ".." in host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (
            parsed.scheme == "http" and not https_only and host in LOCAL_HOSTS
        ))
    ):
        raise ValueError("Se requiere un origen HTTPS válido o HTTP local para pruebas")
    default_port = 443 if parsed.scheme == "https" else 80
    canonical_port = f":{port}" if port is not None and port != default_port else ""
    return f"{parsed.scheme}://{host}{canonical_port}"


PUBLIC_ORIGIN = configured_origin(os.getenv("PUBLIC_URL", "http://localhost:8787"), https_only=False)
PUBLIC_HOST = urlsplit(PUBLIC_ORIGIN).hostname
SECURE_COOKIES = PUBLIC_ORIGIN.startswith("https://")
MOODLE_ORIGINS = tuple(
    configured_origin(item.strip())
    for item in os.getenv("MOODLE_FRAME_ORIGINS", "").split(",")
    if item.strip()
)
if MOODLE_ORIGINS and not SECURE_COOKIES:
    raise ValueError("La incrustación en Moodle requiere HTTPS en Paridad")

COOKIE_PREFIX = "__Host-" if SECURE_COOKIES else ""
SESSION_COOKIE = COOKIE_PREFIX + "paridad"
LOGIN_CSRF_COOKIE = COOKIE_PREFIX + "paridad-login"
SAME_SITE = "none" if MOODLE_ORIGINS else "lax"
CLIENT_DETERRENCE = os.getenv("CLIENT_DETERRENCE", "true").lower() == "true"
TRUST_PROXY_IP = os.getenv("TRUST_PROXY_IP", "false").lower() == "true"
DISPLAY_TZ = ZoneInfo(os.getenv("PARIDAD_TIME_ZONE", "America/Bogota"))
