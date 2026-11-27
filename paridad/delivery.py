"""Submission windows shared by the student and administrator."""

from datetime import UTC, datetime

from fastapi import HTTPException

from .config import DISPLAY_TZ


def parse_window(opens_at: str, closes_at: str) -> tuple[datetime | None, datetime | None]:
    try:
        start, end = (local_instant(value) if value else None for value in (opens_at, closes_at))
    except ValueError:
        raise HTTPException(400, "Revisa la fecha y la hora de las entregas.") from None
    if start and end and start >= end:
        raise HTTPException(400, "El cierre debe ser posterior a la apertura.")
    return start, end


def local_instant(value: str) -> datetime:
    local = datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=DISPLAY_TZ)
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        raise HTTPException(400, "La hora es ambigua o no existe en la zona horaria configurada.")
    return local.astimezone(UTC)


def database_time(conn) -> datetime:
    return conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]


def delivery_status(activity: dict, now: datetime) -> str:
    if not activity["is_open"]:
        return "closed"
    if activity["opens_at"] and now < activity["opens_at"]:
        return "scheduled"
    if activity["closes_at"] and now >= activity["closes_at"]:
        return "expired"
    return "open"
