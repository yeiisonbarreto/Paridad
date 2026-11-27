"""Record the exact selection shown before a bulk deletion is confirmed."""

from uuid import UUID

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .web import view


def deletion_preview(conn, request, user, kind: str, targets: list):
    if not targets:
        raise HTTPException(400, "Selecciona al menos un elemento.")
    row = conn.execute(
        """INSERT INTO bulk_deletions (admin_id, kind, targets, expires_at)
           VALUES (%s, %s, %s, now() + interval '10 minutes') RETURNING id""",
        (user["id"], kind, Jsonb([str(target) for target in targets])),
    ).fetchone()
    return view(
        request, "bulk-delete.html", user=user, kind=kind, token=row["id"], count=len(targets),
    )


def consume_deletion(conn, admin_id: int, kind: str, token: UUID):
    row = conn.execute(
        """DELETE FROM bulk_deletions WHERE id = %s AND admin_id = %s AND kind = %s
           AND expires_at > clock_timestamp() RETURNING targets""",
        (token, admin_id, kind),
    ).fetchone()
    if not row:
        raise HTTPException(409, "La selección ya se usó o venció. Vuelve a seleccionarla.")
    return row["targets"]
