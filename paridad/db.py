"""Parameterized PostgreSQL access and versioned migrations."""

import os
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import BASE_DIR


def database_dsn() -> str:
    return make_conninfo(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "paridad"),
        user=os.getenv("DB_USER", "paridad"),
        password=os.environ.get("DB_PASSWORD") or os.environ["POSTGRES_PASSWORD"],
        connect_timeout=5,
        options="-c timezone=UTC",
    )


def make_pool() -> ConnectionPool:
    return ConnectionPool(
        database_dsn(), kwargs={"row_factory": dict_row}, min_size=1, max_size=10,
        timeout=8, open=False, check=ConnectionPool.check_connection,
    )


@contextmanager
def connection():
    with psycopg.connect(database_dsn(), row_factory=dict_row) as conn:
        yield conn


def migrate() -> None:
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(602541917)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
               version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
            )"""
        )
        for path in sorted((BASE_DIR / "migrations").glob("*.sql")):
            if conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = %s", (path.name,)
            ).fetchone():
                continue
            conn.execute(path.read_text(encoding="utf-8"), prepare=False)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
            print(f"Migración aplicada {path.name}")
        if app_password := os.getenv("PARIDAD_DB_PASSWORD"):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'paridad_app'").fetchone():
                conn.execute("CREATE ROLE paridad_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION")
            conn.execute(sql.SQL("ALTER ROLE paridad_app PASSWORD {}").format(sql.Literal(app_password)))
            conn.execute("GRANT USAGE ON SCHEMA public TO paridad_app")
            conn.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO paridad_app")
            conn.execute(
                """GRANT SELECT, INSERT, UPDATE, DELETE ON
                   users, sessions, rate_limits, settings, languages, activities,
                   activity_languages, submissions, similarity_pairs, student_invitations TO paridad_app"""
            )
