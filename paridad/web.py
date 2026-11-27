"""Shared rendering and HTTP controls."""

from datetime import datetime
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse, RedirectResponse

from .config import BASE_DIR, DISPLAY_TZ, MAX_REQUEST_BYTES, MOODLE_ORIGINS, PUBLIC_ORIGIN, SECURE_COOKIES


def percent(value: int) -> str:
    return f"{value // 100},{value % 100:02d} %"


def date_label(value: datetime) -> str:
    return value.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M %z")


templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters.update(percent=percent, date_label=date_label)
templates.env.globals["paridad_logo_available"] = (BASE_DIR / "static" / "paridad-logo.png").is_file()


def view(request: Request, template: str, *, status: int = 200, **context):
    return templates.TemplateResponse(request, template, context, status_code=status)


def wants_json(request: Request) -> bool:
    return request.headers.get("accept") == "application/json"


def redirect(path: str):
    return RedirectResponse(path, status_code=303)


def request_origin(headers: Headers) -> str:
    origin = headers.get("origin")
    if origin is not None:
        return origin
    try:
        referer = urlsplit(headers.get("referer", ""))
    except ValueError:
        return ""
    return f"{referer.scheme}://{referer.netloc}"


class HTTPControls:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        embedded = MOODLE_ORIGINS and (
            path in {"/", "/login", "/logout"} or path == "/student" or path.startswith("/student/")
        )
        ancestors = " ".join(MOODLE_ORIGINS) if embedded else "'none'"

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["content-security-policy"] = (
                    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                    "img-src 'self'; base-uri 'none'; object-src 'none'; form-action 'self'; "
                    f"frame-ancestors {ancestors}"
                )
                if not embedded:
                    headers["x-frame-options"] = "DENY"
                headers["x-content-type-options"] = "nosniff"
                # Strip the token URL from the Referer without suppressing same-origin form Origin.
                headers["referrer-policy"] = "strict-origin" if path == "/invitacion" else "same-origin"
                headers["permissions-policy"] = "camera=(), microphone=(), geolocation=()"
                headers["cache-control"] = "no-store"
                if SECURE_COOKIES:
                    headers["strict-transport-security"] = "max-age=31536000"
            await send(message)

        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            headers = Headers(scope=scope)
            if request_origin(headers) != PUBLIC_ORIGIN:
                await PlainTextResponse("Origen de la solicitud no permitido.", 403)(scope, receive, send_with_headers)
                return
            # Cap the decoded HTTP request before the multipart parser or file spool.
            payload = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                payload.extend(message.get("body", b""))
                if len(payload) > MAX_REQUEST_BYTES:
                    await PlainTextResponse("Solicitud demasiado grande.", 413)(scope, receive, send_with_headers)
                    return
                if not message.get("more_body", False):
                    break

            async def replay():
                nonlocal payload
                if payload is None:
                    return await receive()
                body = bytes(payload)
                payload = None
                return {"type": "http.request", "body": body, "more_body": False}

            await self.app(scope, replay, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)
