import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from app.db import MIGRATIONS_PATH, apply_migrations
from scripts.migrate_sqlite_to_postgres import (
    PreflightError,
    _extract_check_expressions,
    _normalize_check_expression,
    _normalize_default,
    _parse_postgres_migrations,
    _sqlite_schema,
    _validate_postgres_typed_values,
    open_sqlite_readonly,
    main,
    run_preflight,
)


ROOT = Path(__file__).resolve().parents[2]
POSTGRES_MIGRATIONS = ROOT / "migrations" / "postgresql"
ORIGINAL_SQLITE = ROOT / "instance" / "palavra-do-dia.sqlite3"


class FakePostgresConnection:
    def __init__(self, objects=(), schemas=(), schema_objects=()):
        self.objects = list(objects)
        self.schemas = list(schemas)
        self.schema_objects = list(schema_objects)
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        if not query.lstrip().upper().startswith("SELECT"):
            raise AssertionError("Preflight PostgreSQL must not write.")
        if "current_database()" in query:
            return _FakeResult(
                [("palavra_dia_teste", "postgres", "127.0.0.1", 5432, "on")]
            )
        if "FROM pg_collation" in query:
            return _FakeResult(self.schema_objects)
        if "FROM pg_namespace" in query:
            return _FakeResult(self.schemas)
        if "FROM pg_class" in query:
            return _FakeResult(self.objects)
        if "FROM pg_type" in query or "FROM pg_proc" in query:
            return _FakeResult(())
        if "pg_largeobject_metadata" in query:
            return _FakeResult([(False,)])
        raise AssertionError(f"Unexpected PostgreSQL query: {query}")


class _FakeResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


@pytest.fixture
def sqlite_source(tmp_path):
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
                42, 'synthetic-test', 'Synthetic test', 'pt-BR',
                'Synthetic fixture', 'Test license', 'https://example.invalid/license',
                'Fixture only', 'Test attribution', 'Test reviewer',
                '2026-01-02', 'https://example.invalid/evidence'
            )
            """
        )
    return database_path


def test_sqlite_connection_is_strictly_read_only(tmp_path):
    database = tmp_path / "readonly.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    connection = open_sqlite_readonly(database)
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE forbidden (id INTEGER)")
    finally:
        connection.close()


def test_preflight_reads_original_sqlite_only_and_preserves_its_hash():
    before = hashlib.sha256(ORIGINAL_SQLITE.read_bytes()).hexdigest()
    connection = open_sqlite_readonly(ORIGINAL_SQLITE)
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT COUNT(*) FROM verses"
        ).fetchone()[0] == 31102
    finally:
        connection.close()
    after = hashlib.sha256(ORIGINAL_SQLITE.read_bytes()).hexdigest()
    assert before == after


def test_preflight_reports_counts_empty_tables_and_explicit_id_range(sqlite_source):
    postgres = FakePostgresConnection()

    report = run_preflight(
        sqlite_source,
        postgres,
        POSTGRES_MIGRATIONS,
    )

    assert report["mode"] == "dry-run"
    assert report["transfer_executed"] is False
    assert report["source"]["integrity_check"] == "ok"
    assert report["source"]["foreign_key_errors"] == 0
    assert report["source"]["tables"]["books"]["count"] == 66
    assert report["source"]["tables"]["translations"]["count"] == 1
    assert report["source"]["tables"]["translations"]["id_range"] == {
        "min": 42,
        "max": 42,
    }
    assert report["source"]["tables"]["favorites"]["count"] == 0
    assert report["source"]["tables"]["reading_goals"]["count"] == 0
    assert report["source"]["tables"]["translations"]["primary_key"] == ["id"]
    assert report["destination"]["empty"] is True
    assert report["migration_safety"]["copy_primary_key_values_explicitly"] is True
    assert report["migration_safety"]["single_postgres_transaction_required"] is True
    assert report["migration_safety"]["transfer_implementation_present"] is True
    assert all(query.lstrip().upper().startswith("SELECT") for query in postgres.queries)


def test_preflight_refuses_nonempty_postgres_destination(sqlite_source):
    postgres = FakePostgresConnection(
        objects=[("public", "existing_table", "r")]
    )

    with pytest.raises(PreflightError, match="Destino PostgreSQL"):
        run_preflight(sqlite_source, postgres, POSTGRES_MIGRATIONS)

    assert all(query.lstrip().upper().startswith("SELECT") for query in postgres.queries)


def test_preflight_refuses_nonrelation_objects_in_public_schema(sqlite_source):
    postgres = FakePostgresConnection(
        schema_objects=[("collation", "custom_locale")]
    )

    with pytest.raises(PreflightError, match="objetos de schema"):
        run_preflight(sqlite_source, postgres, POSTGRES_MIGRATIONS)

    assert all(query.lstrip().upper().startswith("SELECT") for query in postgres.queries)


def test_preflight_refuses_schema_column_type_mismatch(sqlite_source, tmp_path):
    migration_copy = tmp_path / "postgresql"
    shutil.copytree(POSTGRES_MIGRATIONS, migration_copy)
    schema_file = migration_copy / "001_scripture_schema.sql"
    contents = schema_file.read_text(encoding="utf-8")
    schema_file.write_text(
        contents.replace(
            "source_sha256 TEXT NOT NULL",
            "source_sha256 BIGINT NOT NULL",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(PreflightError, match=r"Tipo divergente em import_batches.source_sha256"):
        run_preflight(
            sqlite_source,
            FakePostgresConnection(),
            migration_copy,
        )


def test_sqlite_timestamp_default_maps_to_postgresql_current_timestamp():
    assert _normalize_default(
        "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
    ) == "current_timestamp"


def test_check_extraction_handles_nested_parentheses_and_quoted_parentheses():
    sql = "value TEXT CHECK (length(trim(value)) > 0), CHECK (kind IN ('a(b)', 'c'))"

    assert _extract_check_expressions(sql) == [
        "length(trim(value)) > 0",
        "kind in ('a(b)','c')",
    ]
    assert _extract_check_expressions(
        "CHECK (note <> 'CHECK (not a constraint)')"
    ) == ["note <> 'CHECK (not a constraint)'"]


def test_check_normalization_preserves_quoted_string_contents():
    assert _normalize_check_expression(
        "status IN ('Published  Value', 'Mixed (Case)')"
    ) == "status in ('Published  Value','Mixed (Case)')"
    assert _normalize_check_expression(
        "STATUS  IN (\n 'Published  Value',  'Mixed (Case)' )"
    ) == _normalize_check_expression(
        "status in ('Published  Value','Mixed (Case)')"
    )
    assert _normalize_check_expression("status = 'A'") != (
        _normalize_check_expression("status = 'a'")
    )
    assert _normalize_check_expression("status = 'a  b'") != (
        _normalize_check_expression("status = 'a b'")
    )
    assert _normalize_check_expression(
        "status = '__check_literal_0__'"
    ) == "status = '__check_literal_0__'"


def test_sqlite_only_cli_does_not_require_database_url(
    sqlite_source, monkeypatch, capsys
):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    result = main(["--sqlite-only", "--sqlite", str(sqlite_source)])
    report = json.loads(capsys.readouterr().out)

    assert result == 0
    assert report["mode"] == "sqlite-only"
    assert report["destination"] is None
    assert report["transfer_executed"] is False
    assert report["source"]["integrity_check"] == "ok"


def test_preflight_modes_are_mutually_exclusive():
    with pytest.raises(SystemExit) as error:
        main(["--dry-run", "--sqlite-only"])

    assert error.value.code == 2


def test_sqlite_only_reports_check_constraint_differences(sqlite_source):
    report = run_preflight(sqlite_source, sqlite_only=True)
    check_comparison = report["schema_comparison"]["check_comparison"]
    substitutions = {
        (item["table"], item["column"]): item
        for item in check_comparison["type_substitutions"]
    }

    assert check_comparison["unresolved_differences"] == []
    assert report["schema_comparison"]["divergences"] == []
    assert report["schema_comparison"]["matches"] is True
    assert report["ready_for_transfer"] is False
    for table, column in (
        ("daily_readings", "reading_date"),
        ("reading_completions", "completed_date"),
        ("weekly_goal_achievements", "week_start"),
        ("translations", "license_reviewed_at"),
    ):
        assert substitutions[(table, column)]["classification"] == (
            "stronger_postgresql_type"
        )
        assert substitutions[(table, column)]["postgres_type"] == "date"
    assert substitutions[("reflections", "published_at")][
        "postgres_type"
    ] == "timestamp with time zone"
    assert report["source_type_validation"]["incompatible_values"] == 0
    assert report["source_type_validation"]["validated_columns"] > 0


@pytest.mark.parametrize(
    ("table", "column", "postgres_type", "value"),
    [
        ("daily_readings", "reading_date", "date", "2024-02-30"),
        ("reading_completions", "completed_date", "date", ""),
        ("weekly_goal_achievements", "week_start", "date", "not-a-date"),
        ("translations", "license_reviewed_at", "date", "2024-2-03"),
    ],
)
def test_postgres_date_type_validation_rejects_incompatible_values(
    table, column, postgres_type, value
):
    connection = sqlite3.connect(":memory:")
    connection.execute(f'CREATE TABLE "{table}" ("{column}" TEXT NOT NULL)')
    connection.execute(f'INSERT INTO "{table}" ("{column}") VALUES (?)', (value,))
    tables = {
        table: {
            "columns": {
                column: {
                    "type": postgres_type,
                    "nullable": False,
                }
            }
        }
    }

    with pytest.raises(PreflightError, match=rf"{table}\.{column}"):
        _validate_postgres_typed_values(connection, tables)
    connection.close()


@pytest.mark.parametrize(
    "value",
    [
        "2026-02-30T10:00:00Z",
        "not-a-timestamp",
        "2026-02-01T10:00:00",
        "",
    ],
)
def test_postgres_timestamptz_validation_rejects_incompatible_values(value):
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE reflections (published_at TEXT)")
    connection.execute(
        "INSERT INTO reflections (published_at) VALUES (?)",
        (value,),
    )
    tables = {
        "reflections": {
            "columns": {
                "published_at": {
                    "type": "timestamp with time zone",
                    "nullable": True,
                }
            }
        }
    }

    with pytest.raises(PreflightError, match=r"reflections\.published_at"):
        _validate_postgres_typed_values(connection, tables)
    connection.close()


def _assert_timestamptz_precision_or_offset_rejected(value):
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE reflections (published_at TEXT)")
    connection.execute(
        "INSERT INTO reflections (published_at) VALUES (?)",
        (value,),
    )
    tables = {
        "reflections": {
            "columns": {
                "published_at": {
                    "type": "timestamp with time zone",
                    "nullable": True,
                }
            }
        }
    }

    with pytest.raises(PreflightError, match=r"reflections\.published_at"):
        _validate_postgres_typed_values(connection, tables)
    connection.close()


@pytest.mark.parametrize(
    "value",
    [
        "2026-11-06T07:15:00.1234567Z",
        "2026-11-06T07:15:00+16:00",
    ],
)
def test_timestamptz_rejects_precision_or_offset_loss(value):
    _assert_timestamptz_precision_or_offset_rejected(value)


def test_postgres_typed_value_validation_accepts_iso_dates_and_z_timestamps():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE daily_readings (reading_date TEXT)")
    connection.execute("CREATE TABLE reflections (published_at TEXT)")
    connection.execute(
        "INSERT INTO daily_readings (reading_date) VALUES (?)",
        ("2024-02-29",),
    )
    connection.execute(
        "INSERT INTO reflections (published_at) VALUES (?)",
        ("2026-11-06T07:15:00Z",),
    )
    tables = {
        "daily_readings": {
            "columns": {
                "reading_date": {"type": "date", "nullable": False}
            }
        },
        "reflections": {
            "columns": {
                "published_at": {
                    "type": "timestamp with time zone",
                    "nullable": True,
                }
            }
        },
    }

    result = _validate_postgres_typed_values(connection, tables)

    assert result["incompatible_values"] == 0
    assert result["validated_columns"] == 2
    assert result["validated_values"] == 2
    connection.close()


def test_nullable_postgres_datetime_accepts_null_but_not_null_date_rejects_it():
    nullable_connection = sqlite3.connect(":memory:")
    nullable_connection.execute("CREATE TABLE reflections (published_at TEXT)")
    nullable_connection.execute(
        "INSERT INTO reflections (published_at) VALUES (NULL)"
    )
    nullable_tables = {
        "reflections": {
            "columns": {
                "published_at": {
                    "type": "timestamp with time zone",
                    "nullable": True,
                }
            }
        }
    }
    assert _validate_postgres_typed_values(
        nullable_connection, nullable_tables
    )["incompatible_values"] == 0
    nullable_connection.close()

    required_connection = sqlite3.connect(":memory:")
    required_connection.execute("CREATE TABLE translations (reviewed_at TEXT)")
    required_connection.execute(
        "INSERT INTO translations (reviewed_at) VALUES (NULL)"
    )
    required_tables = {
        "translations": {
            "columns": {
                "reviewed_at": {"type": "date", "nullable": False}
            }
        }
    }
    with pytest.raises(PreflightError, match=r"translations\.reviewed_at"):
        _validate_postgres_typed_values(required_connection, required_tables)
    required_connection.close()


def test_sqlite_only_preflight_blocks_invalid_postgres_date(sqlite_source):
    with sqlite3.connect(sqlite_source) as connection:
        connection.execute(
            "UPDATE translations SET license_reviewed_at = '2024-02-30'"
        )

    with pytest.raises(
        PreflightError,
        match=r"translations\.license_reviewed_at.*valid DATE",
    ):
        run_preflight(sqlite_source, sqlite_only=True)


def test_all_expected_integer_primary_keys_are_identity_by_default(sqlite_source):
    report = run_preflight(sqlite_source, sqlite_only=True)
    source_connection = open_sqlite_readonly(sqlite_source)
    try:
        source_tables = _sqlite_schema(source_connection)
    finally:
        source_connection.close()
    identity_divergences = [
        divergence
        for divergence in report["schema_comparison"]["divergences"]
        if divergence["kind"] == "identity"
    ]
    postgres_tables, _ = _parse_postgres_migrations(POSTGRES_MIGRATIONS)
    identities = [
        (table, name, column["identity"])
        for table, metadata in postgres_tables.items()
        for name, column in metadata["columns"].items()
        if column["identity"]
    ]

    assert identity_divergences == []
    assert len(identities) == 12
    assert all(generation == "BY DEFAULT" for _, _, generation in identities)
    assert all(
        source_tables[table]["columns"][name]["type"] == "INTEGER"
        and source_tables[table]["columns"][name]["primary_key"]
        for table, name, _ in identities
    )
    assert postgres_tables["verse_highlights"]["columns"]["favorite_id"][
        "identity"
    ] is None


def test_identity_generation_mismatch_is_reported(sqlite_source, tmp_path):
    migration_copy = tmp_path / "postgresql"
    shutil.copytree(POSTGRES_MIGRATIONS, migration_copy)
    schema_file = migration_copy / "001_scripture_schema.sql"
    contents = schema_file.read_text(encoding="utf-8")
    schema_file.write_text(
        contents.replace(
            "id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
            "id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY",
            1,
        ),
        encoding="utf-8",
    )

    report = run_preflight(
        sqlite_source,
        postgres_migrations_path=migration_copy,
        sqlite_only=True,
    )

    identity_divergences = [
        divergence
        for divergence in report["schema_comparison"]["divergences"]
        if divergence["kind"] == "identity"
    ]
    assert identity_divergences == [
        {
            "kind": "identity",
            "table": "translations",
            "column": "id",
            "expected_generation": "BY DEFAULT",
            "actual_generation": "ALWAYS",
        }
    ]
