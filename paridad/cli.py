"""Commands for the operating-system administrator."""

import argparse
import getpass

from psycopg import errors

from .db import connection, migrate
from .security import PASSWORDS, USERNAME, normalized_username, valid_password


def create_admin(username: str, display_name: str) -> None:
    username = normalized_username(username)
    display_name = display_name.strip()
    if not USERNAME.fullmatch(username) or not 1 <= len(display_name) <= 120:
        raise SystemExit("Nombre de usuario o nombre completo inválido.")
    password = getpass.getpass("Contraseña del administrador (mínimo 12 caracteres): ")
    if not valid_password(password) or password != getpass.getpass("Repite la contraseña: "):
        raise SystemExit("Contraseña inválida o distinta en la confirmación.")
    try:
        with connection() as conn:
            conn.execute(
                "INSERT INTO users (username, display_name, password_hash, role) VALUES (%s, %s, %s, 'ADMIN')",
                (username, display_name, PASSWORDS.hash(password)),
            )
    except errors.UniqueViolation:
        raise SystemExit("Ese nombre de usuario ya existe.") from None
    print(f"Administrador creado: {username}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Administración local de Paridad")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    admin = commands.add_parser("create-admin")
    admin.add_argument("username")
    admin.add_argument("--name", default="Administración")
    args = parser.parse_args()
    if args.command == "migrate":
        migrate()
    else:
        create_admin(args.username, args.name)


if __name__ == "__main__":
    main()
