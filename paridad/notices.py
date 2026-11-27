"""Queue academic review notices and deliver them through the configured SMTP relay."""

import logging
import os
from email.message import EmailMessage

from .config import PUBLIC_ORIGIN, SECURE_COOKIES
from .mailer import send_email, smtp_configured
from .security import normalized_email

LOGGER = logging.getLogger("paridad.notices")
def notices_enabled():
    return bool(smtp_configured() and os.getenv("PARIDAD_NOTICE_FROM", "").strip())


def enqueue_automatic(conn) -> None:
    conn.execute(
        """WITH eligible AS (
               SELECT s.id, s.student_id,
                   NOT s.noticed_similarity AND s.max_similarity_bp >
                       100 * COALESCE(a.similarity_threshold, cfg.similarity_threshold) AS similarity,
                   NOT s.noticed_ai AND s.ai_credit IS NULL AND s.ai_bp >
                       100 * COALESCE(a.ai_threshold, cfg.ai_threshold) AS ai
               FROM submissions s JOIN activities a ON a.id = s.activity_id
               JOIN users u ON u.id = s.student_id CROSS JOIN settings cfg
               WHERE s.state = 'completed' AND u.active AND u.email IS NOT NULL
                 AND ((NOT s.noticed_similarity AND s.max_similarity_bp >
                       100 * COALESCE(a.similarity_threshold, cfg.similarity_threshold))
                   OR (NOT s.noticed_ai AND s.ai_credit IS NULL AND s.ai_bp >
                       100 * COALESCE(a.ai_threshold, cfg.ai_threshold)))
               ORDER BY s.created_at, s.id LIMIT 200 FOR NO KEY UPDATE OF s SKIP LOCKED
           ), queued AS (
               INSERT INTO review_notices (student_id, submission_id, similarity, ai)
               SELECT student_id, id, similarity, ai FROM eligible
               RETURNING submission_id, similarity, ai
           )
           UPDATE submissions s SET noticed_similarity = s.noticed_similarity OR q.similarity,
               noticed_ai = s.noticed_ai OR q.ai FROM queued q WHERE s.id = q.submission_id"""
    )


def enqueue_manual(conn, student_ids):
    # Serialize overlapping clicks across administrators while applying the cooldown.
    conn.execute("SELECT pg_advisory_xact_lock(602541918)")
    rows = conn.execute(
        """INSERT INTO review_notices (student_id)
           SELECT u.id FROM users u WHERE u.id = ANY(%s) AND u.role = 'STUDENT'
             AND u.active AND u.email IS NOT NULL AND NOT EXISTS (
                 SELECT 1 FROM review_notices n WHERE n.student_id = u.id AND n.submission_id IS NULL
                   AND (n.status = 'queued' OR (n.status = 'sent'
                        AND n.created_at > now() - interval '1 minute'))
             )
           RETURNING id""",
        (student_ids,),
    ).fetchall()
    return len(rows)


def notice_message(job):
    message = EmailMessage()
    sender = normalized_email(os.environ["PARIDAD_NOTICE_FROM"])
    message["From"] = f"Paridad <{sender}>"
    domain = sender.rsplit("@", 1)[1]
    message["To"] = job["email"]
    message["Message-ID"] = f"<paridad-{job['id']}@{domain}>"
    message["Subject"] = "Revisa tu trabajo en Paridad"
    if job["submission_id"]:
        reasons = []
        if job["similarity"]:
            reasons.append("la similitud de código")
        if job["ai"]:
            reasons.append("el indicio de IA sin declarar")
        verb = "superaron" if len(reasons) > 1 else "superó"
        detail = (
            f"Te invitamos a revisar «{job['filename']}» de la actividad {job['code']}. "
            f"Paridad detectó que {' y '.join(reasons)} {verb} el umbral de revisión. "
            "El equipo docente puede revisar el contexto de tu entrega.\n"
        )
    else:
        detail = "Te invitamos a revisar el resultado de tus entregas y tu trabajo en Paridad.\n"
    link = f"Consulta tus entregas en {PUBLIC_ORIGIN}/student.\n" if SECURE_COOKIES else "Ingresa a Paridad para ver el resultado.\n"
    message.set_content(
        f"Hola, {job['display_name']}:\n\n{detail}\n{link}\n"
        "Cordialmente,\nParidad\n\nEste correo se generó automáticamente desde Paridad.\n"
    )
    return message


def send_next(conn):
    job = conn.execute(
        """SELECT n.*, u.email, u.active, u.display_name, s.filename, a.code
           FROM review_notices n JOIN users u ON u.id = n.student_id
           LEFT JOIN submissions s ON s.id = n.submission_id LEFT JOIN activities a ON a.id = s.activity_id
           WHERE n.status = 'queued' AND n.available_at <= now()
           ORDER BY n.available_at, n.created_at, n.id LIMIT 1 FOR UPDATE OF n SKIP LOCKED"""
    ).fetchone()
    if not job:
        return False
    if not job["active"] or not job["email"]:
        conn.execute("UPDATE review_notices SET status = 'skipped' WHERE id = %s", (job["id"],))
        return True
    try:
        send_email(notice_message(job))
    except (OSError, ValueError) as exc:
        attempts = job["attempts"] + 1
        LOGGER.warning("Aviso SMTP id=%s intento=%s clase=%s", job["id"], attempts, type(exc).__name__)
        conn.execute(
            """UPDATE review_notices SET attempts = %s, status = %s,
                      available_at = now() + make_interval(mins => %s) WHERE id = %s""",
            (attempts, "failed" if attempts >= 4 else "queued", 2 ** attempts, job["id"]),
        )
    else:
        conn.execute("UPDATE review_notices SET status = 'sent' WHERE id = %s", (job["id"],))
    return True
