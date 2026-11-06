from contextlib import closing
import sqlite3
from pathlib import Path

from flask import current_app, g


MIGRATIONS_PATH = Path(__file__).resolve().parent.parent / "migrations"


def connect_database():
    connection = sqlite3.connect(
        current_app.config["DATABASE_PATH"],
        timeout=5,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def get_db():
    if "database" not in g:
        g.database = connect_database()
    return g.database


def close_db(_error=None):
    connection = g.pop("database", None)
    if connection is not None:
        connection.close()


def init_app(app):
    app.teardown_appcontext(close_db)

    try:
        with closing(
            sqlite3.connect(app.config["DATABASE_PATH"], timeout=5)
        ) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            apply_migrations(connection, MIGRATIONS_PATH)
    except sqlite3.Error as error:
        raise RuntimeError(
            f"Não foi possível inicializar o banco SQLite em "
            f"{app.config['DATABASE_PATH']}"
        ) from error


def apply_migrations(connection, migrations_path):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    connection.commit()

    for migration_file in sorted(Path(migrations_path).glob("[0-9]*.sql")):
        try:
            version_text = migration_file.name.split("_", maxsplit=1)[0]
            version = int(version_text)
        except ValueError as error:
            raise RuntimeError(
                f"Nome de migração inválido: {migration_file.name}"
            ) from error
        if not version_text.isdigit():
            raise RuntimeError(f"Nome de migração inválido: {migration_file.name}")

        applied = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (version,),
        ).fetchone()
        if applied:
            continue

        script = migration_file.read_text(encoding="utf-8")
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            f"{script}\n"
            "INSERT INTO schema_migrations (version) VALUES "
            f"({version});\n"
            "COMMIT;"
        )
