"""Process the durable queue and publish pair results in one transaction."""

import logging
import signal
from threading import Event
from time import monotonic

from psycopg import OperationalError
from psycopg.types.json import Jsonb
from psycopg_pool import PoolTimeout

from .analysis import ANALYSIS_VERSION, Prepared, analyze_style, compare, tokenize
from .db import make_pool

LOGGER = logging.getLogger("paridad.worker")


def process_submission(conn, job: dict) -> None:
    scope = job["activity_id"], job["language_id"]
    lexed = tokenize(job["source"], job["profile"], job["options"])
    normalized = {"tokens": lexed.tokens, "lines": lexed.lines}
    prepared = Prepared.from_analysis(normalized)
    ai_bp, features = analyze_style(lexed)
    max_bp = 0

    conn.execute(
        """UPDATE submissions SET state = 'completed', analyzed_at = now(), analysis = %s,
                  ai_bp = %s, ai_features = %s, analysis_version = %s WHERE id = %s""",
        (Jsonb(normalized), ai_bp, Jsonb(features), ANALYSIS_VERSION, job["id"]),
    )
    with conn.cursor(name="cohort") as cursor:
        cursor.itersize = 8
        cursor.execute(
            """SELECT id, student_id, analysis, analysis_version, max_similarity_bp
               FROM submissions WHERE activity_id = %s AND language_id = %s
                 AND state = 'completed' AND student_id <> %s ORDER BY id""",
            (*scope, job["student_id"]),
        )
        peers = []
        pairs = []
        for peer in cursor:
            if peer["analysis_version"] != ANALYSIS_VERSION:
                raise ValueError("La actividad requiere migrar los análisis a la versión actual")
            if job["id"] < peer["id"]:
                left, right = job, peer
                left_profile, right_profile = prepared, Prepared.from_analysis(peer["analysis"])
            else:
                left, right = peer, job
                left_profile, right_profile = Prepared.from_analysis(peer["analysis"]), prepared
            score, metrics, fragments = compare(left_profile, right_profile)
            max_bp = max(max_bp, score)
            pairs.append((
                *scope, left["id"], left["student_id"], right["id"], right["student_id"],
                score, Jsonb(metrics), Jsonb(fragments), ANALYSIS_VERSION,
            ))
            if score > peer["max_similarity_bp"]:
                peers.append((score, peer["id"]))

    with conn.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO similarity_pairs
               (activity_id, language_id, left_id, left_student_id, right_id, right_student_id,
                score_bp, metrics, fragments, analysis_version)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (left_id, right_id) DO UPDATE SET score_bp = excluded.score_bp,
                 metrics = excluded.metrics, fragments = excluded.fragments,
                 analysis_version = excluded.analysis_version, computed_at = now()""",
            pairs,
        )
        cursor.executemany("UPDATE submissions SET max_similarity_bp = %s WHERE id = %s", peers)
    conn.execute("UPDATE submissions SET max_similarity_bp = %s WHERE id = %s", (max_bp, job["id"]))


def process_next(conn) -> bool:
    scope = conn.execute(
        """SELECT al.activity_id, al.language_id
           FROM activity_languages al
           WHERE EXISTS (
               SELECT 1 FROM submissions s WHERE s.activity_id = al.activity_id AND s.language_id = al.language_id
                 AND s.state = 'pending' AND s.available_at <= now()
           )
           ORDER BY al.activity_id, al.language_id LIMIT 1 FOR NO KEY UPDATE OF al SKIP LOCKED"""
    ).fetchone()
    if not scope:
        return False
    job = conn.execute(
        """SELECT s.*, l.profile, l.options FROM submissions s JOIN languages l ON l.id = s.language_id
           WHERE s.activity_id = %s AND s.language_id = %s AND s.state = 'pending' AND s.available_at <= now()
           ORDER BY s.created_at, s.id LIMIT 1 FOR UPDATE OF s""",
        (scope["activity_id"], scope["language_id"]),
    ).fetchone()
    if not job:
        return False
    try:
        with conn.transaction():
            process_submission(conn, job)
    except OperationalError:
        raise
    except Exception as exc:
        LOGGER.error("Fallo de análisis id=%s clase=%s", job["id"], type(exc).__name__)
        attempts = job["attempts"] + 1
        conn.execute(
            """UPDATE submissions SET attempts = %s, state = %s,
                    available_at = now() + make_interval(secs => %s) WHERE id = %s""",
            (attempts, "failed" if attempts >= 3 else "pending", 10 * attempts, job["id"]),
        )
    return True


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    next_cleanup = 0.0
    with make_pool() as pool:
        while not stop.is_set():
            try:
                if monotonic() >= next_cleanup:
                    with pool.connection() as conn:
                        conn.execute("DELETE FROM sessions WHERE expires_at <= now()")
                        conn.execute("DELETE FROM rate_limits WHERE window_start < now() - interval '1 day'")
                    next_cleanup = monotonic() + 300
                with pool.connection() as conn:
                    processed = process_next(conn)
                if not processed:
                    stop.wait(1)
            except (OperationalError, PoolTimeout) as exc:
                LOGGER.warning("PostgreSQL no disponible clase=%s", type(exc).__name__)
                stop.wait(3)


if __name__ == "__main__":
    run()
