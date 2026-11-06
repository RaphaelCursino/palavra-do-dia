from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import sqlite3
from pathlib import Path
import shutil

import pytest
from flask import Flask

from app import create_app
from app.db import (
    MIGRATIONS_PATH,
    POSTGRES_MIGRATIONS_PATH,
    DatabaseRow,
    _adapt_postgres_query,
    _replace_sqlite_placeholders,
    apply_migrations,
    apply_postgres_migrations,
    begin_transaction,
    connect_database,
    get_database_backend,
    get_db,
    is_integrity_error,
    PostgresConnection,
    postgres_row_factory,
)
from app.services.scripture_import import import_translation
from scripts.postgres_test_guard import PostgresTestSafetyError


def test_database_backend_defaults_to_sqlite_without_database_url():
    assert get_database_backend({"DATABASE_URL": None}) == "sqlite"
    assert get_database_backend({}) == "sqlite"


def test_database_backend_selects_postgresql_from_database_url():
    assert (
        get_database_backend(
            {"DATABASE_URL": "postgresql://example.invalid/database"}
        )
        == "postgresql"
    )


def test_postgres_test_mode_fails_closed_instead_of_falling_back_to_sqlite(
    tmp_path,
):
    database_path = tmp_path / "must-not-be-created.sqlite3"
    app = Flask(__name__)
    app.config.update(
        POSTGRES_TEST_MODE=True,
        DATABASE_URL=None,
        DATABASE_PATH=str(database_path),
    )

    with app.app_context(), pytest.raises(PostgresTestSafetyError) as error:
        connect_database()

    assert error.value.diagnostic_code == "POSTGRES_TEST_DATABASE_URL_REQUIRED"
    assert not database_path.exists()


def test_postgres_test_mode_connects_only_through_the_guard(monkeypatch):
    import scripts.postgres_test_guard as guard

    raw_connection = object()
    calls = []

    def guarded_connect(database_url, row_factory=None):
        calls.append((database_url, row_factory))
        return raw_connection

    monkeypatch.setattr(guard, "connect_postgres_test_database", guarded_connect)
    app = Flask(__name__)
    app.config.update(
        POSTGRES_TEST_MODE=True,
        DATABASE_URL="postgresql://test_role@127.0.0.1:5432/palavra_dia_pytest",
    )

    with app.app_context():
        connection = connect_database()

    assert isinstance(connection, PostgresConnection)
    assert connection._connection is raw_connection
    assert calls == [
        (
            app.config["DATABASE_URL"],
            postgres_row_factory,
        )
    ]


def test_default_sqlite_test_app_ignores_database_url(app):
    assert app.config["DATABASE_URL"] is None
    assert get_database_backend(app.config) == "sqlite"


def test_postgresql_adapter_preserves_query_and_row_compatibility():
    assert _replace_sqlite_placeholders(
        "SELECT * FROM verses WHERE id = ? AND code = ?"
    ) == "SELECT * FROM verses WHERE id = %s AND code = %s"
    row = DatabaseRow({"id": 7, "code": "GEN"})
    assert row["id"] == 7
    assert row[0] == 7
    assert row[1] == "GEN"


def test_postgresql_sql_adaptation_preserves_literals_and_comments():
    query = (
        "SELECT '?' AS literal, value = ? -- ?\n"
        "/* ? */ WHERE note = 'LIKE ? COLLATE NOCASE'"
    )

    assert _replace_sqlite_placeholders(query) == (
        "SELECT '?' AS literal, value = %s -- ?\n"
        "/* ? */ WHERE note = 'LIKE ? COLLATE NOCASE'"
    )
    assert _adapt_postgres_query(
        "SELECT text FROM verses WHERE text LIKE ? COLLATE NOCASE"
    ) == "SELECT text FROM verses WHERE text ILIKE %s"
    assert _adapt_postgres_query(
        "SELECT 'LIKE ? COLLATE NOCASE' AS literal, text = ?"
    ) == "SELECT 'LIKE ? COLLATE NOCASE' AS literal, text = %s"


def test_postgresql_rows_normalize_temporal_values_to_iso_strings():
    class Column:
        def __init__(self, name):
            self.name = name

    class Cursor:
        description = [
            Column("reading_date"),
            Column("created_at"),
            Column("plain_timestamp"),
        ]

    make_row = postgres_row_factory(Cursor())
    row = make_row(
        (
            date(2026, 6, 15),
            datetime(
                2026,
                6,
                15,
                3,
                30,
                tzinfo=timezone(timedelta(hours=3)),
            ),
            datetime(2026, 6, 15, 3, 30),
        )
    )

    assert row["reading_date"] == "2026-06-15"
    assert row["created_at"] == "2026-06-15T00:30:00Z"
    assert row["plain_timestamp"] == "2026-06-15T03:30:00"


def test_postgresql_row_factory_handles_select_results_and_empty_selects():
    class Column:
        def __init__(self, name):
            self.name = name

    class Cursor:
        description = [Column("id"), Column("code")]

    make_row = postgres_row_factory(Cursor())

    assert make_row((7, "GEN")) == {"id": 7, "code": "GEN"}
    assert list(map(make_row, ())) == []


def test_postgresql_row_factory_handles_dml_without_returning():
    class Cursor:
        description = None

    make_row = postgres_row_factory(Cursor())

    assert make_row(()) == {}


def test_postgresql_row_factory_handles_dml_returning_rows():
    class Column:
        def __init__(self, name):
            self.name = name

    class Cursor:
        description = [Column("id")]

    make_row = postgres_row_factory(Cursor())

    assert make_row((12,)) == {"id": 12}


def test_sqlite_select_and_dml_keep_cursor_result_behavior():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, value TEXT)")

    insert_without_returning = connection.execute(
        "INSERT INTO entries (value) VALUES (?)",
        ("first",),
    )
    insert_with_returning = connection.execute(
        "INSERT INTO entries (value) VALUES (?) RETURNING id",
        ("second",),
    )
    update_without_returning = connection.execute(
        "UPDATE entries SET value = ? WHERE id = ?",
        ("updated", 1),
    )
    update_with_returning = connection.execute(
        "UPDATE entries SET value = ? WHERE id = ? RETURNING id",
        ("updated again", 2),
    )
    select_with_result = connection.execute(
        "SELECT id, value FROM entries WHERE value = ?",
        ("updated",),
    )
    select_without_result = connection.execute(
        "SELECT id, value FROM entries WHERE value = ?",
        ("missing",),
    )

    assert insert_without_returning.description is None
    assert insert_with_returning.fetchone() == (2,)
    assert update_without_returning.description is None
    assert update_with_returning.fetchone() == (2,)
    assert select_with_result.fetchone() == (1, "updated")
    assert select_without_result.fetchone() is None
    connection.close()


def test_postgresql_adapter_executemany_uses_and_closes_cursor():
    class FakeCursor:
        def __init__(self, events):
            self.events = events

        def __enter__(self):
            self.events.append("cursor_enter")
            return self

        def __exit__(self, exception_type, exception, traceback):
            self.events.append(("cursor_exit", exception_type))

        def executemany(self, query, parameters):
            self.events.append(("executemany", query, parameters))

    class FakeConnection:
        def __init__(self):
            self.events = []

        def cursor(self):
            self.events.append("cursor")
            return FakeCursor(self.events)

        def commit(self):
            self.events.append("commit")

        def rollback(self):
            self.events.append("rollback")

    raw = FakeConnection()
    result = PostgresConnection(raw).executemany(
        "INSERT INTO books (code) VALUES (?)",
        [("GEN",), ("EXO",)],
    )

    assert result is None
    assert raw.events == [
        "cursor",
        "cursor_enter",
        ("executemany", "INSERT INTO books (code) VALUES (%s)", [("GEN",), ("EXO",)]),
        ("cursor_exit", None),
    ]


def test_postgresql_adapter_executemany_closes_cursor_and_propagates_errors():
    failure = RuntimeError("insert failed")

    class FakeCursor:
        def __init__(self, events):
            self.events = events

        def __enter__(self):
            self.events.append("cursor_enter")
            return self

        def __exit__(self, exception_type, exception, traceback):
            self.events.append(("cursor_exit", exception_type))

        def executemany(self, query, parameters):
            self.events.append(("executemany", query, parameters))
            raise failure

    class FakeConnection:
        def __init__(self):
            self.events = []

        def cursor(self):
            self.events.append("cursor")
            return FakeCursor(self.events)

        def commit(self):
            self.events.append("commit")

        def rollback(self):
            self.events.append("rollback")

    raw = FakeConnection()
    with pytest.raises(RuntimeError, match="insert failed"):
        PostgresConnection(raw).executemany(
            "INSERT INTO books (code) VALUES (?)",
            [("GEN",)],
        )

    assert raw.events == [
        "cursor",
        "cursor_enter",
        ("executemany", "INSERT INTO books (code) VALUES (%s)", [("GEN",)]),
        ("cursor_exit", RuntimeError),
    ]


def test_postgresql_adapter_context_manager_delegates_commit_and_rollback():
    class FakeConnection:
        def __init__(self):
            self.events = []

        def __enter__(self):
            self.events.append("enter")
            return self

        def __exit__(self, exception_type, exception, traceback):
            self.events.append(("exit", exception_type))
            return False

        def commit(self):
            self.events.append("commit")

        def rollback(self):
            self.events.append("rollback")

        def close(self):
            self.events.append("close")

    raw = FakeConnection()
    with PostgresConnection(raw) as wrapped:
        wrapped.commit()
    assert raw.events == ["enter", "commit", ("exit", None)]

    raw = FakeConnection()
    try:
        with PostgresConnection(raw):
            raise ValueError("rollback test")
    except ValueError:
        pass
    assert raw.events == ["enter", ("exit", ValueError)]


def test_transaction_and_integrity_helpers_support_sqlite_and_postgres_shapes():
    connection = sqlite3.connect(":memory:")
    begin_transaction(connection)
    connection.rollback()
    assert is_integrity_error(sqlite3.IntegrityError("duplicate"))

    class FakePostgresConnection:
        def __init__(self):
            self.statement = None

        def execute(self, statement):
            self.statement = statement

    postgres_connection = FakePostgresConnection()
    begin_transaction(postgres_connection)
    assert postgres_connection.statement == "BEGIN"


def test_sqlite_savepoint_rolls_back_only_nested_changes():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE values_table (value INTEGER)")
    connection.execute("BEGIN")
    connection.execute("INSERT INTO values_table VALUES (1)")
    connection.execute("SAVEPOINT nested")
    connection.execute("INSERT INTO values_table VALUES (2)")
    connection.execute("ROLLBACK TO SAVEPOINT nested")
    connection.execute("RELEASE SAVEPOINT nested")
    connection.commit()
    assert connection.execute(
        "SELECT value FROM values_table ORDER BY value"
    ).fetchall() == [(1,)]


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


def test_postgresql_migrations_define_schema_without_seed_data():
        migration_text = "\n".join(
            migration.read_text(encoding="utf-8")
            for migration in sorted(POSTGRES_MIGRATIONS_PATH.glob("*.sql"))
        )
        expected_tables = {
            "translations",
            "books",
            "chapters",
            "import_batches",
            "verses",
            "daily_readings",
            "reflections",
            "favorites",
            "reading_completions",
            "reading_goals",
            "verse_notes",
            "verse_annotations",
            "verse_highlights",
            "prayer_entries",
            "reader_preferences",
            "achievement_unlocks",
            "weekly_goal_achievements",
        }
        declared_tables = set(
            re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+)", migration_text)
        )
        assert expected_tables <= declared_tables
        assert not re.search(r"(?im)^\s*INSERT\s+INTO\s+", migration_text)
        assert "GENERATED BY DEFAULT AS IDENTITY" in migration_text
        assert "TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP" in migration_text
        assert "DATE NOT NULL" in migration_text
        assert re.search(
            r"favorite_id BIGINT PRIMARY KEY\s+REFERENCES favorites\(id\)",
            migration_text,
        )
        assert not re.search(
            r"favorite_id BIGINT GENERATED BY DEFAULT AS IDENTITY",
            migration_text,
        )
        assert re.search(r"source_sha256 TEXT NOT NULL", migration_text)
        assert "REFERENCES verses" in migration_text
        assert "PRIMARY KEY (owner_key, achievement_code)" in migration_text
        assert "CREATE INDEX idx_favorites_owner_created" in migration_text


def test_postgresql_migrations_do_not_apply_text_functions_to_temporal_columns():
        migration_text = "\n".join(
            migration.read_text(encoding="utf-8")
            for migration in sorted(POSTGRES_MIGRATIONS_PATH.glob("*.sql"))
        )
        temporal_columns = re.findall(
            r"(?im)^\s*(\w+)\s+(?:DATE|TIMESTAMPTZ)\b",
            migration_text,
        )

        for column in temporal_columns:
            assert not re.search(
                rf"\b(?:trim|length|lower|upper)\s*\(\s*{column}\b",
                migration_text,
                re.IGNORECASE,
            ), f"text function applied to temporal column {column}"

        assert re.search(
            r"\(status\s*=\s*'published'\s+AND\s+published_at\s+IS\s+NOT\s+NULL\)",
            migration_text,
            re.IGNORECASE,
        )


class _FakePostgresResult:
        def __init__(self, value=None):
            self.value = value

        def fetchone(self):
            return self.value


class _FakePostgresMigrationConnection:
        def __init__(self, fail_on_script=False):
            self.applied = set()
            self.events = []
            self.fail_on_script = fail_on_script

        def execute(self, query, parameters=()):
            self.events.append(("execute", query, parameters))
            if query.startswith("SELECT 1"):
                return _FakePostgresResult(
                    (1,) if parameters[0] in self.applied else None
                )
            if query.startswith("INSERT INTO schema_migrations"):
                self.applied.add(parameters[0])
            if self.fail_on_script and query.startswith("CREATE TABLE"):
                raise RuntimeError("simulated migration failure")
            return _FakePostgresResult()

        def commit(self):
            self.events.append(("commit",))

        def rollback(self):
            self.events.append(("rollback",))


def test_postgresql_migration_runner_is_idempotent(tmp_path):
        migrations = tmp_path / "postgresql"
        migrations.mkdir()
        (migrations / "001_first.sql").write_text(
            "CREATE TABLE first_table (id BIGINT PRIMARY KEY);",
            encoding="utf-8",
        )
        connection = _FakePostgresMigrationConnection()

        apply_postgres_migrations(connection, migrations)
        apply_postgres_migrations(connection, migrations)

        assert connection.applied == {1}
        assert sum(event == ("commit",) for event in connection.events) == 3
        assert not any(event == ("rollback",) for event in connection.events)


def test_postgresql_migration_runner_rolls_back_failed_migration(tmp_path):
        migrations = tmp_path / "postgresql"
        migrations.mkdir()
        (migrations / "001_broken.sql").write_text(
            "CREATE TABLE broken_table (id BIGINT PRIMARY KEY);",
            encoding="utf-8",
        )
        connection = _FakePostgresMigrationConnection(fail_on_script=True)

        with pytest.raises(RuntimeError, match="simulated migration failure"):
            apply_postgres_migrations(connection, migrations)

        assert ("rollback",) in connection.events
        assert connection.applied == set()


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
            RETURNING id
            """,
            (
                reading_location["translation_id"],
                reading_location["chapter_id"],
            ),
        ).fetchone()["id"]
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
