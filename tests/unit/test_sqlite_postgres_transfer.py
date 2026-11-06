import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.db import MIGRATIONS_PATH, apply_migrations
from scripts import sqlite_postgres_transfer as transfer
from scripts.migrate_sqlite_to_postgres import (
    POSTGRES_MIGRATIONS_PATH,
    PreflightError,
    _parse_postgres_migrations,
    _topological_order,
    open_sqlite_readonly,
)


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def migrated_sqlite(tmp_path):
    database_path = tmp_path / "source.sqlite3"
    with sqlite3.connect(database_path) as connection:
        apply_migrations(connection, MIGRATIONS_PATH)
        connection.execute(
            """
            INSERT INTO translations (
                id, code, name, language_code, rights_holder, license_name,
                license_url, license_scope, attribution_text,
                license_reviewed_by, license_reviewed_at,
                license_evidence_url
            ) VALUES (
                42, 'synthetic-test', 'Fixture Bíblia 📖', 'pt-BR', 'Fixture owner',
                'Fixture license', 'https://example.invalid/license',
                'Fixture scope', 'Fixture attribution', 'Reviewer',
                '2026-01-02', 'https://example.invalid/evidence'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO import_batches (
                id, translation_id, source_name, source_url, acquired_at,
                source_sha256, importer_version, imported_at, book_count,
                chapter_count, verse_count
            ) VALUES (
                23, 42, 'Synthetic fixture', 'https://example.invalid/source',
                '2026-01-01', ?, 'test', '2026-01-01T10:00:00.000000Z',
                1, 1, 1
            )
            """,
            ("a" * 64,),
        )
        connection.execute(
            "INSERT INTO chapters (id, book_id, chapter_number) VALUES (9001, 1, 1)"
        )
        connection.execute(
            """
            INSERT INTO verses (
                id, translation_id, chapter_id, verse_number, text,
                import_batch_id
            ) VALUES (9002, 42, 9001, 1, 'Texto sintético com fé 📖', 23)
            """
        )
        connection.execute(
            """
            INSERT INTO daily_readings (
                id, reading_date, translation_id, chapter_id,
                start_verse_number, end_verse_number, created_at
            ) VALUES (
                9003, '2026-01-04', 42, 9001, 1, 1,
                '2026-01-01T10:00:00.000000Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO reflections (
                id, daily_reading_id, title, reflection_text, simple_message,
                practical_application, prayer_text, reflection_question,
                author, status, published_at, created_at
            ) VALUES (
                9004, 9003, 'Reflexão sintética', 'Texto', 'Mensagem',
                'Aplicação', 'Oração', 'Pergunta', 'Teste', 'draft', NULL,
                '2026-01-01T10:00:00.000000Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO verse_notes (id, verse_id, note_type, ordinal, note_text)
            VALUES (9005, 9002, 'footnote', 1, 'Nota sintética')
            """
        )
        connection.execute(
            """
            INSERT INTO favorites (
                id, owner_key, favorite_type, translation_id, chapter_id,
                verse_number, verse_text, reference_text, reflection_id,
                created_at
            ) VALUES (
                9100, 'visitor-α', 'verse', 42, 9001, 1,
                'Texto sintético com fé 📖', 'Gênesis 1:1', NULL,
                '2026-01-01T10:00:00.000000Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO verse_highlights (
                favorite_id, color, category, updated_at
            ) VALUES (
                9100, 'gold', 'faith', '2026-01-01T10:00:00.000000Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO prayer_entries (
                id, owner_key, entry_type, title, description, status,
                created_at, updated_at, answered_at
            ) VALUES (
                9200, 'visitor-α', 'request', 'Pedido sintético',
                'Descrição sintética', 'active', '2026-01-01T10:00:00.000000Z',
                '2026-01-01T10:00:00.000000Z', NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO reader_preferences (
                owner_key, theme, font_scale, verse_spacing, updated_at
            ) VALUES (
                'visitor-α', 'dark', 120, 'compact',
                '2026-01-01T10:00:00.000000Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO reading_goals (owner_key, weekly_target, updated_at)
            VALUES ('visitor-α', 3, '2026-01-01T10:00:00.000000Z')
            """
        )
        connection.execute(
            """
            INSERT INTO reading_completions (
                id, owner_key, daily_reading_id, completed_date, completed_at
            ) VALUES (
                9300, 'visitor-α', 9003, '2026-01-04',
                '2026-01-04T10:00:00.000000Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO verse_annotations (
                id, owner_key, verse_id, note_text, created_at, updated_at
            ) VALUES (
                9400, 'visitor-α', 9002, 'Anotação sintética',
                '2026-01-01T10:00:00.000000Z',
                '2026-01-01T10:00:00.000000Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO achievement_unlocks (
                owner_key, achievement_code, unlocked_at
            ) VALUES (
                'visitor-α', 'first_reading',
                '2026-01-04T10:00:00.000000Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO weekly_goal_achievements (
                owner_key, week_start, weekly_target, achieved_at
            ) VALUES (
                'visitor-α', '2025-12-29', 3,
                '2026-01-04T10:00:00.000000Z'
            )
            """
        )
    return database_path


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.position = 0

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def fetchmany(self, size):
        batch = self.rows[self.position : self.position + size]
        self.position += len(batch)
        return batch


class MigrationCursor:
    def __init__(self, connection):
        self.connection = connection

    def executemany(self, query, rows):
        self.connection.executemany(query, rows)

    def close(self):
        pass


class MigrationConnection:
    def __init__(
        self,
        tables,
        indexes,
        objects=(),
        identity=None,
        default_overrides=None,
    ):
        self.tables = tables
        self.indexes = indexes
        self.objects = list(objects)
        self.identity = identity or (
            "palavra_dia_teste",
            "postgres",
            "127.0.0.1/32",
            5432,
            "off",
        )
        self.default_overrides = default_overrides or {}
        self.constraint_rows_override = None
        self.not_null_constraints_override = None
        self.actual_indexes = set(indexes)
        self.executed_writes = []
        self.staged_writes = []
        self.inserted_rows = {}
        self.inserted_values = {}
        self.rollback_count = 0
        self.committed = False
        self.migration_sql = []
        self.query_parameters = []

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        self.query_parameters.append((query, params))
        if "CREATE TABLE" in query.upper() and "schema_migrations" not in query:
            self.migration_sql.append(query)
        if "current_database()" in query:
            return Result([self.identity])
        if "FROM pg_collation" in query:
            return Result()
        if "FROM pg_class" in query:
            return Result(self.objects)
        if "FROM pg_namespace" in query:
            return Result()
        if "FROM pg_type" in query or "FROM pg_proc" in query:
            return Result()
        if "FROM information_schema.columns" in query:
            rows = []
            for table, metadata in self.tables.items():
                for column, details in metadata["columns"].items():
                    identity = details["identity"]
                    default = self.default_overrides.get(
                        (table, column),
                        details["default"],
                    )
                    if (
                        default is not None
                        and details["type"] == "text"
                        and default.startswith("'")
                    ):
                        default += "::text"
                    rows.append(
                        (
                            table,
                            column,
                            details["type"],
                            "YES" if details["nullable"] else "NO",
                            "YES" if identity else "NO",
                            identity,
                            default,
                        )
                    )
            return Result(self.column_rows_override or rows)
        if "FROM pg_constraint" in query:
            if "con.contype = 'n'" in query:
                if self.not_null_constraints_override is not None:
                    rows = list(self.not_null_constraints_override)
                else:
                    rows = [
                        (table, column, True)
                        for table, metadata in self.tables.items()
                        for column, details in metadata["columns"].items()
                        if not details["nullable"]
                    ]
                    rows.extend(
                        [
                            ("schema_migrations", "applied_at", True),
                            ("schema_migrations", "version", True),
                        ]
                    )
                if params is not None:
                    included_tables = set(params[0])
                    rows = [
                        row for row in rows if row[0] in included_tables
                    ]
                return Result(rows)
            if self.constraint_rows_override is not None:
                return Result(self.constraint_rows_override)
            rows = []
            for table, metadata in self.tables.items():
                counts = {
                    "p": int(bool(metadata["primary_key"])),
                    "f": len(metadata["foreign_keys"]),
                    "u": len(metadata["unique_keys"]),
                    "c": len(metadata["check_constraints"]),
                }
                rows.extend(
                    (table, kind, count, True)
                    for kind, count in counts.items()
                    if count
                )
            return Result(rows)
        if "FROM pg_indexes" in query:
            return Result([(name,) for name in self.actual_indexes])
        if normalized.startswith("SELECT pg_advisory_xact_lock"):
            return Result([(None,)])
        if query.lstrip().upper().startswith("SELECT"):
            return Result([(0,)])

        self.executed_writes.append(normalized)
        self.staged_writes.append(normalized)
        return Result()

    def executemany(self, query, rows):
        table = query.split("INTO ", 1)[1].split(" ", 1)[0].strip('"')
        batch = list(rows)
        self.inserted_rows[table] = self.inserted_rows.get(table, 0) + len(batch)
        self.inserted_values.setdefault(table, []).extend(batch)
        self.executed_writes.append(query)
        self.staged_writes.append(query)

    def cursor(self):
        return MigrationCursor(self)

    def rollback(self):
        self.rollback_count += 1
        self.staged_writes.clear()
        self.inserted_rows.clear()

    def commit(self):
        self.committed = True
        self.staged_writes.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        if _args[0] is not None:
            self.rollback()
        return False

    @property
    def column_rows_override(self):
        return getattr(self, "_column_rows_override", None)

    @column_rows_override.setter
    def column_rows_override(self, rows):
        self._column_rows_override = rows


def _migration_connection(objects=(), identity=None):
    tables, indexes = _parse_postgres_migrations(POSTGRES_MIGRATIONS_PATH)
    return MigrationConnection(tables, indexes, objects, identity)


def test_verified_backup_is_unique_integral_and_keeps_source_unchanged(
    migrated_sqlite, tmp_path
):
    before = hashlib.sha256(migrated_sqlite.read_bytes()).hexdigest()

    first = transfer.create_verified_backup(
        migrated_sqlite,
        tmp_path / "external-backups",
    )
    second = transfer.create_verified_backup(
        migrated_sqlite,
        tmp_path / "external-backups",
    )

    assert first["path"] != second["path"]
    assert first["integrity_check"] == "ok"
    assert first["foreign_key_errors"] == 0
    assert first["outside_project"] is True
    assert Path(first["path"]).is_file()
    assert transfer._sha256_file(first["path"]) == first["sha256"]
    assert hashlib.sha256(migrated_sqlite.read_bytes()).hexdigest() == before


def test_verified_backup_refuses_a_directory_inside_the_project(migrated_sqlite):
    with pytest.raises(PreflightError, match="fora da pasta do projeto"):
        transfer.create_verified_backup(
            migrated_sqlite,
            ROOT / "backups-forbidden-by-test",
        )


def test_load_order_satisfies_every_foreign_key_dependency():
    tables, _indexes = _parse_postgres_migrations(POSTGRES_MIGRATIONS_PATH)
    order, dependencies = _topological_order(tables)
    positions = {table: index for index, table in enumerate(order)}

    assert len(order) == 17
    for table, referenced_tables in dependencies.items():
        assert all(
            positions[referenced] < positions[table]
            for referenced in referenced_tables
        )


def test_insert_preserves_ids_relationships_and_personal_data(
    migrated_sqlite,
):
    source = open_sqlite_readonly(migrated_sqlite)
    try:
        tables, _indexes = _parse_postgres_migrations(
            POSTGRES_MIGRATIONS_PATH
        )
        schema = transfer._sqlite_transfer_schema(source, tables)
        order, _dependencies = _topological_order(tables)
        destination = _migration_connection(tables, {})

        counts = transfer._insert_source_data(
            source,
            destination,
            schema,
            order,
        )
    finally:
        source.close()

    assert counts["translations"] == 1
    assert counts["favorites"] == 1
    assert destination.inserted_rows["translations"] == 1
    assert len(destination.inserted_rows) == 17
    assert any('"translations" ("id", ' in query for query in destination.executed_writes)
    translation_metadata = schema["translations"]
    id_position = list(translation_metadata["columns"]).index("id")
    assert destination.inserted_values["translations"][0][id_position] == 42
    assert destination.inserted_rows["books"] == 66
    favorite_metadata = schema["favorites"]
    favorite_owner_position = list(favorite_metadata["columns"]).index(
        "owner_key"
    )
    assert (
        destination.inserted_values["favorites"][0][favorite_owner_position]
        == "visitor-α"
    )
    verse_metadata = schema["verses"]
    verse_positions = {
        column: index
        for index, column in enumerate(verse_metadata["columns"])
    }
    verse_row = destination.inserted_values["verses"][0]
    assert verse_row[verse_positions["id"]] == 9002
    assert verse_row[verse_positions["chapter_id"]] == 9001
    assert verse_row[verse_positions["import_batch_id"]] == 23
    reflection_metadata = schema["reflections"]
    published_position = list(reflection_metadata["columns"]).index(
        "published_at"
    )
    assert destination.inserted_values["reflections"][0][published_position] is None


def test_adaptation_preserves_null_dates_timestamps_and_unicode():
    assert transfer._adapt_value(None, "date") is None
    assert transfer._adapt_value("2024-02-29", "date").isoformat() == "2024-02-29"
    timestamp = transfer._adapt_value(
        "2026-11-06T07:15:00.123456Z",
        "timestamp with time zone",
    )
    assert timestamp.isoformat() == "2026-11-06T07:15:00.123456+00:00"
    assert transfer._adapt_value("Fé e esperança 📖", "text") == (
        "Fé e esperança 📖"
    )


def test_checksum_changes_when_preserved_content_changes(migrated_sqlite):
    source = open_sqlite_readonly(migrated_sqlite)
    try:
        tables, _indexes = _parse_postgres_migrations(
            POSTGRES_MIGRATIONS_PATH
        )
        schema = transfer._sqlite_transfer_schema(source, tables)
        original = transfer.compute_canonical_checksums(source, schema)
    finally:
        source.close()

    copy_path = migrated_sqlite.parent / "altered.sqlite3"
    with sqlite3.connect(migrated_sqlite) as source, sqlite3.connect(
        copy_path
    ) as copy:
        source.backup(copy)
        copy.execute(
            "UPDATE translations SET name = 'Changed fixture' WHERE id = 42"
        )

    changed = open_sqlite_readonly(copy_path)
    try:
        changed_schema = transfer._sqlite_transfer_schema(changed, tables)
        altered = transfer.compute_canonical_checksums(
            changed,
            changed_schema,
        )
    finally:
        changed.close()

    assert original["groups"]["bible"]["sha256"] != (
        altered["groups"]["bible"]["sha256"]
    )


def test_count_and_checksum_mismatches_are_rejected():
    with pytest.raises(PreflightError, match="Contagens por tabela"):
        transfer._assert_table_counts_match(
            {"verses": 3},
            {"verses": 2},
            "teste",
        )
    with pytest.raises(PreflightError, match="Checksum canônico"):
        transfer._assert_checksums_match(
            {"groups": {"bible": {"sha256": "expected"}}},
            {"groups": {"bible": {"sha256": "different"}}},
            "teste",
        )


def test_foreign_key_integrity_check_finds_orphans(migrated_sqlite):
    connection = sqlite3.connect(migrated_sqlite)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute(
        """
        INSERT INTO daily_readings (
            id, reading_date, translation_id, chapter_id,
            start_verse_number, end_verse_number
        ) VALUES (1, '2026-01-01', 42, 99999, 1, 1)
        """
    )
    tables, _indexes = _parse_postgres_migrations(POSTGRES_MIGRATIONS_PATH)

    with pytest.raises(PreflightError, match="Chaves estrangeiras órfãs"):
        transfer._validate_foreign_key_integrity(connection, tables)
    connection.close()


def test_all_identity_sequences_are_restarted_with_next_value():
    tables, _indexes = _parse_postgres_migrations(POSTGRES_MIGRATIONS_PATH)

    class SequenceConnection:
        def __init__(self):
            self.restart_statements = []

        def execute(self, query):
            if query.startswith("SELECT MAX"):
                return Result([(None,)])
            self.restart_statements.append(query)
            return Result()

    connection = SequenceConnection()
    restarted = transfer._restart_identity_sequences(connection, tables)

    assert len(restarted) == 12
    assert set(restarted.values()) == {1}
    assert len(connection.restart_statements) == 12
    assert "verse_highlights.favorite_id" not in restarted


def test_actual_schema_validation_checks_defaults_and_identity():
    tables, indexes = _parse_postgres_migrations(POSTGRES_MIGRATIONS_PATH)
    connection = _migration_connection()

    report = transfer._validate_actual_postgres_schema(
        connection,
        tables,
        indexes,
    )

    assert report["tables"] == 17
    assert report["constraints_validated"] is True


def test_postgresql_18_not_null_constraints_are_counted_and_compared_by_column():
    tables, indexes = _parse_postgres_migrations(POSTGRES_MIGRATIONS_PATH)
    connection = _migration_connection()
    translations_not_null = [
        row
        for row in connection.execute(
            """
            SELECT c.relname, a.attname, con.convalidated
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL unnest(con.conkey) AS key(attnum)
            JOIN pg_attribute a
              ON a.attrelid = c.oid AND a.attnum = key.attnum
            WHERE n.nspname = 'public' AND con.contype = 'n'
            """
        ).fetchall()
        if row[0] == "translations"
    ]

    report = transfer._validate_actual_postgres_schema(
        connection,
        tables,
        indexes,
    )

    assert len(translations_not_null) == 14
    assert report["constraints_validated"] is True


def test_not_null_query_excludes_control_table_but_covers_all_app_tables():
    tables, indexes = _parse_postgres_migrations(POSTGRES_MIGRATIONS_PATH)
    connection = _migration_connection()

    report = transfer._validate_actual_postgres_schema(
        connection,
        tables,
        indexes,
    )

    not_null_query, parameters = next(
        (query, params)
        for query, params in connection.query_parameters
        if "con.contype = 'n'" in query
    )
    included_tables = set(parameters[0])

    assert len(included_tables) == 17
    assert included_tables == set(tables)
    assert "schema_migrations" not in included_tables
    assert "c.relname = ANY(%s)" in not_null_query
    assert report["constraints_validated"] is True
    assert report["indexes"] == len(indexes)


@pytest.mark.parametrize(
    ("actual_columns", "missing", "unexpected"),
    [
        (
            None,
            [("translations", "license_status")],
            [],
        ),
        (
            [
                (table, column, True)
                for table, metadata in _parse_postgres_migrations(
                    POSTGRES_MIGRATIONS_PATH
                )[0].items()
                for column, details in metadata["columns"].items()
                if not details["nullable"]
            ]
            + [("translations", "unexpected_column", True)],
            [],
            [("translations", "unexpected_column")],
        ),
        (
            [
                (table, column, True)
                for table, metadata in _parse_postgres_migrations(
                    POSTGRES_MIGRATIONS_PATH
                )[0].items()
                for column, details in metadata["columns"].items()
                if not details["nullable"]
            ]
            + [("translations", "id", True)],
            [],
            [],
        ),
    ],
)
def test_postgresql_18_not_null_column_divergence_is_rejected(
    actual_columns,
    missing,
    unexpected,
):
    tables, indexes = _parse_postgres_migrations(POSTGRES_MIGRATIONS_PATH)
    connection = _migration_connection()
    if actual_columns is None:
        actual_columns = [
            (table, column, True)
            for table, metadata in tables.items()
            for column, details in metadata["columns"].items()
            if not details["nullable"]
        ]
        actual_columns.remove(("translations", "license_status", True))
    connection.not_null_constraints_override = actual_columns

    with pytest.raises(PreflightError) as error:
        transfer._validate_actual_postgres_schema(
            connection,
            tables,
            indexes,
        )

    assert error.value.diagnostic_code == "SCHEMA_CONSTRAINT_MISMATCH"
    details = error.value.diagnostic_details
    assert details["constraint_type"] == "not_null"
    assert details["missing_columns"] == missing
    assert details["unexpected_columns"] == unexpected
    assert details["expected_count"] != details["actual_count"]
    if details["duplicate_columns"]:
        assert details["duplicate_columns"] == [("translations", "id")]


def test_diagnostic_applies_schema_only_and_always_rolls_back():
    connection = _migration_connection()
    tables, indexes = _parse_postgres_migrations(POSTGRES_MIGRATIONS_PATH)

    report = transfer.diagnose_postgres_schema(connection)

    assert report["mode"] == "diagnose-local-schema"
    assert report["status"] == "validated"
    assert report["diagnostic_code"] is None
    assert report["migration_versions_applied_in_transaction"] == list(
        range(1, 9)
    )
    assert len(connection.migration_sql) == 8
    assert report["schema_validation"]["tables"] == len(tables)
    assert report["schema_validation"]["indexes"] == len(indexes)
    assert report["data_transfer_started"] is False
    assert report["backup_created"] is False
    assert report["sqlite_accessed"] is False
    assert report["commit_executed"] is False
    assert report["rollback_confirmed"] is True
    assert connection.rollback_count >= 3
    assert connection.committed is False
    assert connection.staged_writes == []
    assert connection.inserted_rows == {}


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("schema-validation", "SCHEMA_INDEX_MISMATCH"),
        ("migration", "DIAGNOSTIC_SCHEMA_MIGRATION_FAILED"),
    ],
)
def test_diagnostic_failure_returns_safe_code_and_rolls_back(
    failure,
    expected_code,
):
    connection = _migration_connection()
    if failure == "schema-validation":
        connection.actual_indexes.remove(next(iter(connection.actual_indexes)))
    else:
        original_execute = connection.execute

        def fail_migration(query, params=None):
            if "CREATE TABLE reflections" in query:
                raise RuntimeError("private connection detail")
            return original_execute(query, params)

        connection.execute = fail_migration

    report = transfer.diagnose_postgres_schema(connection)

    assert report["status"] == (
        "schema_mismatch" if failure == "schema-validation" else "aborted"
    )
    assert report["diagnostic_code"] == expected_code
    assert "private connection detail" not in repr(report)
    assert report["data_transfer_started"] is False
    assert report["backup_created"] is False
    assert report["sqlite_accessed"] is False
    assert report["commit_executed"] is False
    assert report["rollback_confirmed"] is True
    assert report["migration_versions_applied_in_transaction"] == (
        [1, 2] if failure == "migration" else list(range(1, 9))
    )
    assert connection.committed is False
    assert connection.staged_writes == []
    assert connection.rollback_count >= 2


def test_diagnostic_refuses_unauthorized_or_nonempty_targets_without_migrations():
    unauthorized = _migration_connection(
        identity=(
            "other_database",
            "postgres",
            "127.0.0.1/32",
            5432,
            "off",
        )
    )
    nonempty = _migration_connection(objects=[("public", "existing", "r")])

    for connection, expected_code in (
        (unauthorized, "TARGET_IDENTITY_MISMATCH"),
        (nonempty, "TARGET_NOT_EMPTY"),
    ):
        report = transfer.diagnose_postgres_schema(connection)
        assert report["diagnostic_code"] == expected_code
        assert report["migration_versions_applied_in_transaction"] == []
        assert report["rollback_confirmed"] is True
        assert connection.migration_sql == []
        assert connection.committed is False


def test_diagnostic_reports_when_rollback_cannot_be_confirmed():
    connection = _migration_connection()
    original_rollback = connection.rollback

    def rollback_with_residual_object():
        original_rollback()
        if connection.rollback_count == 2:
            connection.objects.append(("public", "residual_object", "r"))

    connection.rollback = rollback_with_residual_object

    report = transfer.diagnose_postgres_schema(connection)

    assert report["status"] == "aborted"
    assert report["diagnostic_code"] == "DIAGNOSTIC_ROLLBACK_NOT_CONFIRMED"
    assert report["rollback_confirmed"] is False
    assert report["data_transfer_started"] is False
    assert connection.committed is False


def test_diagnostic_cli_never_calls_sqlite_transfer(
    monkeypatch,
    capsys,
):
    from types import SimpleNamespace

    import getpass
    import scripts.migrate_sqlite_to_postgres as cli

    class InteractiveInput:
        def isatty(self):
            return True

    connection = _migration_connection()
    connection.actual_indexes.remove(next(iter(connection.actual_indexes)))
    terminal_input = InteractiveInput()
    monkeypatch.setattr(cli.sys, "stdin", terminal_input)
    monkeypatch.setattr(cli.sys, "__stdin__", terminal_input)
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "local-test-password")
    monkeypatch.setitem(
        __import__("sys").modules,
        "psycopg",
        SimpleNamespace(connect=lambda **_kwargs: connection),
    )

    def forbidden_transfer(*_args, **_kwargs):
        pytest.fail("diagnostic mode must never invoke SQLite transfer")

    monkeypatch.setattr(transfer, "migrate_to_postgres", forbidden_transfer)

    result = cli.main(["--diagnose-local-schema"])
    report = json.loads(capsys.readouterr().out)

    assert result == 1
    assert report["mode"] == "diagnose-local-schema"
    assert report["diagnostic_code"] == "SCHEMA_INDEX_MISMATCH"
    assert report["rollback_confirmed"] is True
    assert report["data_transfer_started"] is False
    assert connection.committed is False


def test_diagnostic_cli_aborts_context_if_rollback_is_unconfirmed(
    monkeypatch,
    capsys,
):
    from types import SimpleNamespace

    import getpass
    import scripts.migrate_sqlite_to_postgres as cli

    class InteractiveInput:
        def isatty(self):
            return True

    connection = _migration_connection()
    terminal_input = InteractiveInput()
    monkeypatch.setattr(cli.sys, "stdin", terminal_input)
    monkeypatch.setattr(cli.sys, "__stdin__", terminal_input)
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "local-test-password")
    monkeypatch.setitem(
        __import__("sys").modules,
        "psycopg",
        SimpleNamespace(connect=lambda **_kwargs: connection),
    )
    monkeypatch.setattr(
        transfer,
        "diagnose_postgres_schema",
        lambda _connection: {
            "status": "aborted",
            "rollback_confirmed": False,
        },
    )
    monkeypatch.setattr(
        transfer,
        "migrate_to_postgres",
        lambda *_args, **_kwargs: pytest.fail(
            "diagnostic mode must never invoke SQLite transfer"
        ),
    )

    result = cli.main(["--diagnose-local-schema"])
    output = capsys.readouterr()

    assert result == 2
    assert "code=DIAGNOSTIC_ROLLBACK_NOT_CONFIRMED" in output.err
    assert connection.rollback_count == 1
    assert connection.committed is False


def test_actual_schema_validation_rejects_default_mismatch():
    tables, indexes = _parse_postgres_migrations(POSTGRES_MIGRATIONS_PATH)
    connection = _migration_connection()
    connection.default_overrides[("translations", "license_status")] = (
        "'rejected'"
    )

    with pytest.raises(PreflightError, match="DEFAULT PostgreSQL divergente"):
        transfer._validate_actual_postgres_schema(
            connection,
            tables,
            indexes,
        )


@pytest.mark.parametrize(
    ("mismatch", "expected_code"),
    [
        ("tables", "SCHEMA_TABLES_MISMATCH"),
        ("columns", "SCHEMA_COLUMNS_MISMATCH"),
        ("type", "SCHEMA_COLUMN_TYPE_MISMATCH"),
        ("nullability", "SCHEMA_NULLABILITY_MISMATCH"),
        ("identity", "SCHEMA_IDENTITY_MISMATCH"),
        ("default", "SCHEMA_DEFAULT_MISMATCH"),
        ("constraints", "SCHEMA_CONSTRAINT_MISMATCH"),
        ("unvalidated-constraint", "SCHEMA_CONSTRAINT_MISMATCH"),
        ("indexes", "SCHEMA_INDEX_MISMATCH"),
    ],
)
def test_actual_schema_validation_returns_specific_safe_diagnostics(
    mismatch,
    expected_code,
):
    expected, indexes = _parse_postgres_migrations(
        POSTGRES_MIGRATIONS_PATH
    )
    connection = _migration_connection()
    if mismatch == "tables":
        connection.tables.pop("books")
    elif mismatch == "columns":
        connection.tables["books"]["columns"].pop("code")
    elif mismatch == "type":
        connection.tables["books"]["columns"]["code"]["type"] = "integer"
    elif mismatch == "nullability":
        connection.tables["books"]["columns"]["code"]["nullable"] = True
    elif mismatch == "identity":
        connection.tables["translations"]["columns"]["id"]["identity"] = None
    elif mismatch == "default":
        connection.default_overrides[
            ("translations", "license_status")
        ] = "'private fixture text'"
    elif mismatch == "constraints":
        connection.tables["books"]["unique_keys"].clear()
    elif mismatch == "unvalidated-constraint":
        rows = connection.execute(
            "SELECT * FROM pg_constraint"
        ).fetchall()
        table, kind, count, _validated = rows[0]
        connection.constraint_rows_override = [
            (row_table, row_kind, row_count, False)
            if (row_table, row_kind) == (table, kind)
            else (row_table, row_kind, row_count, validated)
            for row_table, row_kind, row_count, validated in rows
        ]
    elif mismatch == "indexes":
        connection.actual_indexes.remove(next(iter(indexes)))

    with pytest.raises(PreflightError) as error:
        transfer._validate_actual_postgres_schema(
            connection,
            expected,
            indexes,
        )

    assert error.value.diagnostic_code == expected_code
    assert error.value.diagnostic_details
    assert "private fixture text" not in repr(error.value.diagnostic_details)


def test_postgresql_default_normalization_handles_typed_text_literals():
    assert transfer._normalize_postgres_default("'approved'::text") == (
        "'approved'"
    )
    assert transfer._normalize_postgres_default("CURRENT_TIMESTAMP") == (
        "current_timestamp"
    )
    assert transfer._normalize_postgres_default(None) is None


@pytest.mark.parametrize("failure_stage", ["after_schema", "after_data"])
def test_transfer_failure_rolls_back_all_staged_postgres_writes(
    migrated_sqlite,
    tmp_path,
    monkeypatch,
    failure_stage,
):
    monkeypatch.setattr(transfer, "DEFAULT_SQLITE_PATH", migrated_sqlite)
    connection = _migration_connection()
    original_hash = transfer._sha256_file(migrated_sqlite)

    def fail_at(stage):
        if stage == failure_stage:
            raise RuntimeError("intentional test failure")

    with pytest.raises(PreflightError) as error:
        transfer.migrate_to_postgres(
            connection,
            sqlite_path=migrated_sqlite,
            backup_root=tmp_path / "backups",
            expected_source_sha256=original_hash,
            failure_hook=fail_at,
        )

    assert connection.rollback_count == 2
    expected_code = (
        "POSTGRES_SCHEMA_MIGRATION_FAILED"
        if failure_stage == "after_schema"
        else "DATA_TRANSFER_FAILED"
    )
    assert error.value.diagnostic_code == expected_code
    assert connection.staged_writes == []
    assert connection.committed is False
    assert hashlib.sha256(migrated_sqlite.read_bytes()).hexdigest() == original_hash


def test_schema_diagnostic_survives_transfer_wrapper_and_rolls_back(
    migrated_sqlite,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(transfer, "DEFAULT_SQLITE_PATH", migrated_sqlite)
    connection = _migration_connection()
    connection.actual_indexes.remove(next(iter(connection.actual_indexes)))

    with pytest.raises(PreflightError) as error:
        transfer.migrate_to_postgres(
            connection,
            sqlite_path=migrated_sqlite,
            backup_root=tmp_path / "backups",
            expected_source_sha256=transfer._sha256_file(migrated_sqlite),
        )

    assert error.value.diagnostic_code == "SCHEMA_INDEX_MISMATCH"
    assert error.value.diagnostic_details["missing_indexes"]
    assert connection.rollback_count == 2
    assert connection.staged_writes == []
    assert connection.committed is False


def test_migration_refuses_nonempty_and_unauthorized_destinations(
    migrated_sqlite, tmp_path, monkeypatch
):
    monkeypatch.setattr(transfer, "DEFAULT_SQLITE_PATH", migrated_sqlite)
    nonempty = _migration_connection(objects=[("public", "existing", "r")])
    unauthorized = _migration_connection(
        identity=(
            "another_database",
            "postgres",
            "127.0.0.1/32",
            5432,
            "off",
        )
    )
    source_hash = transfer._sha256_file(migrated_sqlite)

    for connection in (nonempty, unauthorized):
        with pytest.raises(PreflightError):
            transfer.migrate_to_postgres(
                connection,
                sqlite_path=migrated_sqlite,
                backup_root=tmp_path / "backups",
                expected_source_sha256=source_hash,
            )
        assert connection.executed_writes == []
        assert connection.committed is False
    assert list((tmp_path / "backups").glob("*.sqlite3")) == []


def test_reexecution_refuses_previously_populated_destination(
    migrated_sqlite, tmp_path, monkeypatch
):
    monkeypatch.setattr(transfer, "DEFAULT_SQLITE_PATH", migrated_sqlite)
    connection = _migration_connection(
        objects=[("public", "translations", "r")]
    )

    with pytest.raises(PreflightError) as error:
        transfer.migrate_to_postgres(
            connection,
            sqlite_path=migrated_sqlite,
            backup_root=tmp_path / "backups",
            expected_source_sha256=transfer._sha256_file(migrated_sqlite),
        )

    assert error.value.diagnostic_code == "DESTINATION_PREFLIGHT_FAILED"
    assert connection.executed_writes == []
    assert not (tmp_path / "backups").exists()


def test_cli_uses_only_fixed_target_and_does_not_print_password(
    migrated_sqlite, monkeypatch, capsys
):
    from types import SimpleNamespace

    import getpass
    import scripts.migrate_sqlite_to_postgres as cli

    class InteractiveInput:
        def isatty(self):
            return True

    terminal_input = InteractiveInput()

    class ContextConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    captured = {}

    def connect(**kwargs):
        captured.update(kwargs)
        return ContextConnection()

    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "not-for-output")
    monkeypatch.setattr(cli.sys, "stdin", terminal_input)
    monkeypatch.setattr(cli.sys, "__stdin__", terminal_input)
    monkeypatch.setitem(
        __import__("sys").modules,
        "psycopg",
        SimpleNamespace(connect=connect),
    )
    monkeypatch.setattr(
        cli,
        "run_preflight",
        lambda *_args, **_kwargs: {"mode": "dry-run"},
    )

    assert cli.main(["--dry-run", "--sqlite", str(migrated_sqlite)]) == 0
    output = capsys.readouterr().out

    assert "not-for-output" not in output
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 5432
    assert captured["dbname"] == "palavra_dia_teste"
    assert captured["user"] == "postgres"
    assert captured["password"] == "not-for-output"
    assert captured["options"] == "-c default_transaction_read_only=on"


def test_cli_refuses_password_prompt_without_hidden_interactive_input(
    migrated_sqlite, monkeypatch, capsys
):
    import getpass
    import scripts.migrate_sqlite_to_postgres as cli

    class NonInteractiveInput:
        def isatty(self):
            return False

    stdin = NonInteractiveInput()
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "__stdin__", stdin)
    monkeypatch.setattr(
        getpass,
        "getpass",
        lambda _prompt: pytest.fail("getpass must not use a visible fallback"),
    )

    result = cli.main(
        ["--dry-run", "--sqlite", str(migrated_sqlite)]
    )
    output = capsys.readouterr()

    assert result == 2
    assert "code=INTERACTIVE_TERMINAL_REQUIRED" in output.err
