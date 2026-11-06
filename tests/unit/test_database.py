from contextlib import closing
import hashlib
import json
import sqlite3
from pathlib import Path
import shutil

import pytest

from app import create_app
from app.db import MIGRATIONS_PATH, apply_migrations, get_db
from app.services.scripture_import import import_translation


def test_app_initializes_scripture_schema_without_translation_content(app):
    database_path = app.config["DATABASE_PATH"]

    with app.app_context():
        connection = get_db()
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        book_count = connection.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        translation_count = connection.execute(
            "SELECT COUNT(*) FROM translations"
        ).fetchone()[0]
        daily_readings_count = connection.execute(
            "SELECT COUNT(*) FROM daily_readings"
        ).fetchone()[0]
        reflections_count = connection.execute(
            "SELECT COUNT(*) FROM reflections"
        ).fetchone()[0]
        progress_completion_count = connection.execute(
            "SELECT COUNT(*) FROM reading_completions"
        ).fetchone()[0]
        progress_goal_count = connection.execute(
            "SELECT COUNT(*) FROM reading_goals"
        ).fetchone()[0]

    assert database_path.endswith("test.sqlite3")
    assert [row["version"] for row in versions] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert book_count == 66
    assert translation_count == 0
    assert daily_readings_count == 0
    assert reflections_count == 0
    assert progress_completion_count == 0
    assert progress_goal_count == 0
    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)


def test_schema_migrations_are_idempotent(app):
    database_path = app.config["DATABASE_PATH"]
    second_app = create_app({"DATABASE_PATH": database_path})

    with second_app.app_context():
        connection = get_db()
        assert connection.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 66
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == 8


def test_favorites_migration_preserves_existing_scripture_and_reflection_data(
    tmp_path, demo_payload, license_approval
):
    legacy_migrations = tmp_path / "legacy-migrations"
    legacy_migrations.mkdir()
    for migration in MIGRATIONS_PATH.glob("[0-9][0-9][0-9]_*.sql"):
        if migration.name.startswith(("001_", "002_", "003_")):
            shutil.copyfile(migration, legacy_migrations / migration.name)

    database_path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        apply_migrations(connection, legacy_migrations)
        payload_bytes = json.dumps(
            demo_payload,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        import_translation(
            connection,
            demo_payload,
            license_approval,
            hashlib.sha256(payload_bytes).hexdigest(),
        )
        reading_location = connection.execute(
            """
            SELECT translations.id AS translation_id, chapters.id AS chapter_id
            FROM translations
            JOIN books ON books.code = 'GEN'
            JOIN chapters ON chapters.book_id = books.id
            WHERE translations.code = 'demo-test'
              AND chapters.chapter_number = 1
            """
        ).fetchone()
        reading_id = connection.execute(
            """
            INSERT INTO daily_readings (
                reading_date, translation_id, chapter_id,
                start_verse_number, end_verse_number
            ) VALUES ('2026-06-15', ?, ?, 1, 2)
            """,
            (
                reading_location["translation_id"],
                reading_location["chapter_id"],
            ),
        ).lastrowid
        connection.execute(
            """
            INSERT INTO reflections (
                daily_reading_id, title, reflection_text, simple_message,
                practical_application, prayer_text, reflection_question,
                author, status, published_at
            ) VALUES (?, 'Título de teste', 'Reflexão de teste',
                      'Mensagem', 'Aplicação', 'Oração', 'Pergunta',
                      'Autoria', 'published', '2026-06-15T12:00:00Z')
            """,
            (reading_id,),
        )
        connection.commit()

        tables = (
            "books",
            "translations",
            "import_batches",
            "chapters",
            "verses",
            "daily_readings",
            "reflections",
        )
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }
        preserved_rows = {
            "translation": tuple(
                connection.execute(
                    "SELECT code, name FROM translations WHERE code = 'demo-test'"
                ).fetchone()
            ),
            "verse": tuple(
                connection.execute(
                    """
                    SELECT text FROM verses
                    JOIN translations ON translations.id = verses.translation_id
                    WHERE translations.code = 'demo-test' AND verse_number = 1
                    """
                ).fetchone()
            ),
            "daily_reading": tuple(
                connection.execute(
                    "SELECT reading_date FROM daily_readings WHERE id = ?",
                    (reading_id,),
                ).fetchone()
            ),
            "reflection": tuple(
                connection.execute(
                    "SELECT title FROM reflections WHERE daily_reading_id = ?",
                    (reading_id,),
                ).fetchone()
            ),
        }

        apply_migrations(connection, MIGRATIONS_PATH)
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }
        preserved_rows_after = {
            "translation": tuple(
                connection.execute(
                    "SELECT code, name FROM translations WHERE code = 'demo-test'"
                ).fetchone()
            ),
            "verse": tuple(
                connection.execute(
                    """
                    SELECT text FROM verses
                    JOIN translations ON translations.id = verses.translation_id
                    WHERE translations.code = 'demo-test' AND verse_number = 1
                    """
                ).fetchone()
            ),
            "daily_reading": tuple(
                connection.execute(
                    "SELECT reading_date FROM daily_readings WHERE id = ?",
                    (reading_id,),
                ).fetchone()
            ),
            "reflection": tuple(
                connection.execute(
                    "SELECT title FROM reflections WHERE daily_reading_id = ?",
                    (reading_id,),
                ).fetchone()
            ),
        }

        assert after == before
        assert preserved_rows_after == preserved_rows
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()[-1]["version"] == 8
        assert connection.execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM reading_completions"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM reading_goals"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM verse_notes"
        ).fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_invalid_database_path_has_a_clear_startup_error(tmp_path):
    database_path = tmp_path / "not-a-database"
    database_path.mkdir()

    with pytest.raises(RuntimeError, match="Não foi possível inicializar o banco SQLite"):
        create_app({"DATABASE_PATH": str(database_path)})


def test_invalid_daily_reading_timezone_has_a_clear_startup_error(tmp_path):
    with pytest.raises(RuntimeError, match="fuso horário IANA válido"):
        create_app(
            {
                "DATABASE_PATH": str(tmp_path / "test.sqlite3"),
                "DAILY_READING_TIMEZONE": "Invalid/Timezone",
            }
        )
