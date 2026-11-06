from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from scripts.migration_errors import PreflightError

from scripts.migrate_sqlite_to_postgres import (
    DEFAULT_SQLITE_PATH,
    POSTGRES_MIGRATIONS_PATH,
    _parse_postgres_migrations,
    _quote_identifier,
    _sha256_file,
    _topological_order,
    inspect_postgres_destination,
    open_sqlite_readonly,
    run_preflight,
)


EXPECTED_SOURCE_SHA256 = (
    "1ea55aba74e1c23882403b0b8dcee729931bcd260d23212bfad1e56b2b824e22"
)
AUTHORIZED_DATABASE = "palavra_dia_teste"
AUTHORIZED_USER = "postgres"
AUTHORIZED_HOST = "127.0.0.1"
AUTHORIZED_PORT = 5432
BACKUP_ROOT = (
    Path.home() / "Desktop" / "PalavraDoDia-backups"
)

CONTENT_CHECKSUM_MANIFEST = {
    "bible": (
        "translations",
        "import_batches",
        "books",
        "chapters",
        "verses",
    ),
    "editorial": (
        "daily_readings",
        "reflections",
        "verse_notes",
    ),
    "personal": (
        "favorites",
        "prayer_entries",
        "reader_preferences",
        "reading_completions",
        "reading_goals",
        "verse_annotations",
        "verse_highlights",
        "achievement_unlocks",
        "weekly_goal_achievements",
    ),
}
CONTENT_CHECKSUM_COLUMNS = {
    "achievement_unlocks": ("owner_key", "achievement_code", "unlocked_at"),
    "books": ("id", "code", "name_pt", "canonical_order", "testament"),
    "chapters": ("id", "book_id", "chapter_number"),
    "daily_readings": (
        "id",
        "reading_date",
        "translation_id",
        "chapter_id",
        "start_verse_number",
        "end_verse_number",
        "created_at",
    ),
    "favorites": (
        "id",
        "owner_key",
        "favorite_type",
        "translation_id",
        "chapter_id",
        "verse_number",
        "verse_text",
        "reference_text",
        "reflection_id",
        "created_at",
    ),
    "import_batches": (
        "id",
        "translation_id",
        "source_name",
        "source_url",
        "acquired_at",
        "source_sha256",
        "importer_version",
        "imported_at",
        "book_count",
        "chapter_count",
        "verse_count",
    ),
    "prayer_entries": (
        "id",
        "owner_key",
        "entry_type",
        "title",
        "description",
        "status",
        "created_at",
        "updated_at",
        "answered_at",
    ),
    "reader_preferences": (
        "owner_key",
        "theme",
        "font_scale",
        "verse_spacing",
        "updated_at",
    ),
    "reading_completions": (
        "id",
        "owner_key",
        "daily_reading_id",
        "completed_date",
        "completed_at",
    ),
    "reading_goals": ("owner_key", "weekly_target", "updated_at"),
    "reflections": (
        "id",
        "daily_reading_id",
        "title",
        "reflection_text",
        "simple_message",
        "practical_application",
        "prayer_text",
        "reflection_question",
        "author",
        "status",
        "published_at",
        "created_at",
    ),
    "translations": (
        "id",
        "code",
        "name",
        "language_code",
        "rights_holder",
        "license_name",
        "license_url",
        "license_scope",
        "attribution_text",
        "license_reviewed_by",
        "license_reviewed_at",
        "license_evidence_url",
        "license_status",
        "created_at",
    ),
    "verse_annotations": (
        "id",
        "owner_key",
        "verse_id",
        "note_text",
        "created_at",
        "updated_at",
    ),
    "verse_highlights": ("favorite_id", "color", "category", "updated_at"),
    "verse_notes": ("id", "verse_id", "note_type", "ordinal", "note_text"),
    "verses": (
        "id",
        "translation_id",
        "chapter_id",
        "verse_number",
        "text",
        "import_batch_id",
    ),
    "weekly_goal_achievements": (
        "owner_key",
        "week_start",
        "weekly_target",
        "achieved_at",
    ),
}
CONTENT_CHECKSUM_PRIMARY_KEYS = {
    "achievement_unlocks": ("owner_key", "achievement_code"),
    "books": ("id",),
    "chapters": ("id",),
    "daily_readings": ("id",),
    "favorites": ("id",),
    "import_batches": ("id",),
    "prayer_entries": ("id",),
    "reader_preferences": ("owner_key",),
    "reading_completions": ("id",),
    "reading_goals": ("owner_key",),
    "reflections": ("id",),
    "translations": ("id",),
    "verse_annotations": ("id",),
    "verse_highlights": ("favorite_id",),
    "verse_notes": ("id",),
    "verses": ("id",),
    "weekly_goal_achievements": ("owner_key", "week_start"),
}


def _json_line(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _normalize_timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    else:
        raise PreflightError("Tipo inválido para checksum TIMESTAMPTZ.")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PreflightError("TIMESTAMPTZ sem fuso não pode ser canonicalizado.")
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _canonical_value(value, postgres_type):
    if value is None:
        return None
    if postgres_type == "date":
        if isinstance(value, datetime):
            raise PreflightError("TIMESTAMP encontrado onde DATE era esperado.")
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, str):
            try:
                parsed = date.fromisoformat(value)
            except ValueError as error:
                raise PreflightError("Valor DATE inválido no checksum.") from error
            if parsed.isoformat() != value:
                raise PreflightError("Valor DATE fora do formato canônico.")
            return value
        raise PreflightError("Tipo inválido para checksum DATE.")
    if postgres_type == "timestamp with time zone":
        return _normalize_timestamp(value)
    if postgres_type in {"bigint", "integer"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise PreflightError("Tipo inválido para checksum inteiro.")
        return value
    if postgres_type == "text":
        if not isinstance(value, str):
            raise PreflightError("Tipo inválido para checksum TEXT.")
        return value
    raise PreflightError(f"Tipo PostgreSQL não coberto pelo checksum: {postgres_type}.")


def _table_rows(connection, table, columns, primary_key, postgres):
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    quoted_table = _quote_identifier(table)
    order = ", ".join(_quote_identifier(column) for column in primary_key)
    cursor = connection.execute(
        f"SELECT {quoted_columns} FROM {quoted_table} ORDER BY {order}"
    )
    while True:
        batch = cursor.fetchmany(500)
        if not batch:
            break
        for row in batch:
            if postgres:
                yield tuple(row)
            else:
                yield tuple(row[column] for column in columns)


def _table_count(connection, table):
    return connection.execute(
        f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
    ).fetchone()[0]


def _assert_table_counts_match(expected, actual, stage):
    if expected != actual:
        raise PreflightError(
            f"Contagens por tabela divergiram em {stage}."
        )


def _assert_checksums_match(expected, actual, stage):
    if expected != actual:
        raise PreflightError(
            f"Checksum canônico de conteúdo divergiu em {stage}."
        )


def compute_canonical_checksums(connection, schema, postgres=False):
    table_digests = {}
    group_hashers = {}
    group_counts = Counter()
    expected_tables = {
        table
        for tables in CONTENT_CHECKSUM_MANIFEST.values()
        for table in tables
    }
    if expected_tables != set(CONTENT_CHECKSUM_COLUMNS):
        raise PreflightError("Manifesto de checksum incompleto ou inconsistente.")
    if set(schema) != expected_tables:
        raise PreflightError(
            "As tabelas disponíveis não correspondem ao manifesto canônico."
        )
    for group in CONTENT_CHECKSUM_MANIFEST:
        group_hashers[group] = hashlib.sha256()
        group_hashers[group].update(
            _json_line(
                {
                    "format": "palavra-do-dia-content",
                    "version": 1,
                    "dataset": group,
                }
            )
        )

    for group, group_tables in CONTENT_CHECKSUM_MANIFEST.items():
        for table in group_tables:
            metadata = schema[table]
            columns = CONTENT_CHECKSUM_COLUMNS[table]
            primary_key = CONTENT_CHECKSUM_PRIMARY_KEYS[table]
            if (
                set(metadata["columns"]) != set(columns)
                or tuple(metadata["primary_key"]) != primary_key
            ):
                raise PreflightError(
                    f"Esquema não corresponde ao manifesto em {table}."
                )
            table_types = {
                column: metadata["columns"][column]["type"]
                for column in columns
            }
            count = _table_count(connection, table)
            table_hasher = hashlib.sha256()
            table_hasher.update(
                _json_line(
                    {
                        "format": "palavra-do-dia-table",
                        "version": 1,
                        "table": table,
                    }
                )
            )
            table_hasher.update(
                _json_line(
                    {
                        "table": table,
                        "columns": list(columns),
                        "row_count": count,
                    }
                )
            )
            group_hashers[group].update(
                _json_line(
                    {
                        "table": table,
                        "columns": list(columns),
                        "row_count": count,
                    }
                )
            )
            actual_count = 0
            for row in _table_rows(
                connection,
                table,
                columns,
                primary_key,
                postgres,
            ):
                canonical_row = [
                    _canonical_value(value, table_types[column])
                    for column, value in zip(columns, row)
                ]
                encoded = _json_line(canonical_row)
                table_hasher.update(encoded)
                group_hashers[group].update(encoded)
                actual_count += 1
            if actual_count != count:
                raise PreflightError(
                    f"Contagem instável ao calcular checksum em {table}."
                )
            table_digests[table] = table_hasher.hexdigest()
            group_counts[group] += count

    return {
        "format": "palavra-do-dia-content-v1",
        "groups": {
            group: {
                "sha256": hasher.hexdigest(),
                "rows": group_counts[group],
                "tables": list(CONTENT_CHECKSUM_MANIFEST[group]),
            }
            for group, hasher in group_hashers.items()
        },
        "tables": table_digests,
    }


def _adapt_value(value, postgres_type):
    if value is None:
        return None
    if postgres_type == "date":
        if isinstance(value, date):
            return value
        return date.fromisoformat(value)
    if postgres_type == "timestamp with time zone":
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(
                value[:-1] + "+00:00" if value.endswith("Z") else value
            )
        )
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise PreflightError("Timestamp sem fuso não pode ser migrado.")
        return parsed
    return value


def _normalize_postgres_default(value):
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", str(value).strip()).lower()
    return re.sub(
        r"::(?:text|integer|bigint|date|timestamp with time zone)$",
        "",
        normalized,
    )


def _validate_actual_postgres_schema(connection, expected_tables, expected_indexes):
    actual_tables = connection.execute(
        """
        SELECT table_name, column_name, data_type, is_nullable,
               is_identity, identity_generation, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name <> 'schema_migrations'
        ORDER BY table_name, ordinal_position
        """
    ).fetchall()
    actual_columns = {}
    for (
        table,
        column,
        data_type,
        nullable,
        identity,
        generation,
        default,
    ) in actual_tables:
        actual_columns.setdefault(table, {})[column] = {
            "type": data_type,
            "nullable": nullable == "YES",
            "identity": generation if identity == "YES" else None,
            "default": _normalize_postgres_default(default),
        }

    if set(actual_columns) != set(expected_tables):
        raise PreflightError(
            "Tabelas PostgreSQL divergentes após as migrações.",
            diagnostic_code="SCHEMA_TABLES_MISMATCH",
            diagnostic_details={
                "expected_tables": sorted(expected_tables),
                "actual_tables": sorted(actual_columns),
            },
        )
    for table, expected in expected_tables.items():
        if set(actual_columns[table]) != set(expected["columns"]):
            raise PreflightError(
                f"Colunas PostgreSQL divergentes em {table}.",
                diagnostic_code="SCHEMA_COLUMNS_MISMATCH",
                diagnostic_details={
                    "table": table,
                    "expected_columns": sorted(expected["columns"]),
                    "actual_columns": sorted(actual_columns[table]),
                },
            )
        for column, expected_column in expected["columns"].items():
            actual = actual_columns[table][column]
            location = {"table": table, "column": column}
            if actual["type"] != expected_column["type"]:
                raise PreflightError(
                    f"Tipo PostgreSQL divergente em {table}.{column}.",
                    diagnostic_code="SCHEMA_COLUMN_TYPE_MISMATCH",
                    diagnostic_details={
                        **location,
                        "expected_type": expected_column["type"],
                        "actual_type": actual["type"],
                    },
                )
            if actual["nullable"] != expected_column["nullable"]:
                raise PreflightError(
                    f"Nulabilidade PostgreSQL divergente em {table}.{column}.",
                    diagnostic_code="SCHEMA_NULLABILITY_MISMATCH",
                    diagnostic_details={
                        **location,
                        "expected_nullable": expected_column["nullable"],
                        "actual_nullable": actual["nullable"],
                    },
                )
            if actual["identity"] != expected_column["identity"]:
                raise PreflightError(
                    f"IDENTITY PostgreSQL divergente em {table}.{column}.",
                    diagnostic_code="SCHEMA_IDENTITY_MISMATCH",
                    diagnostic_details={
                        **location,
                        "expected_generation": expected_column["identity"],
                        "actual_generation": actual["identity"],
                    },
                )
            expected_default = _normalize_postgres_default(
                expected_column["default"]
            )
            if actual["default"] != expected_default:
                raise PreflightError(
                    f"DEFAULT PostgreSQL divergente em {table}.{column}.",
                    diagnostic_code="SCHEMA_DEFAULT_MISMATCH",
                    diagnostic_details={
                        **location,
                        "expected_default_present": expected_default is not None,
                        "actual_default_present": actual["default"] is not None,
                    },
                )

    expected_constraint_counts = {}
    for table, metadata in expected_tables.items():
        expected_constraint_counts[table] = {
            "p": int(bool(metadata["primary_key"])),
            "f": len(metadata["foreign_keys"]),
            "u": len(metadata["unique_keys"]),
            "c": len(metadata["check_constraints"]),
        }
    constraints = connection.execute(
        """
        SELECT c.relname, con.contype, COUNT(*), BOOL_AND(con.convalidated)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND con.contype <> 'n'
        GROUP BY c.relname, con.contype
        """
    ).fetchall()
    actual_constraint_counts = {}
    for table, kind, count, validated in constraints:
        actual_constraint_counts.setdefault(table, {})[kind] = count
        if not validated:
            raise PreflightError(
                f"Constraint PostgreSQL não validada em {table}.",
                diagnostic_code="SCHEMA_CONSTRAINT_MISMATCH",
                diagnostic_details={
                    "table": table,
                    "constraint_type": kind,
                    "validated": False,
                },
            )
    for table, expected in expected_constraint_counts.items():
        if actual_constraint_counts.get(table, {}) != {
            kind: count
            for kind, count in expected.items()
            if count
        }:
            raise PreflightError(
                f"Contagem de constraints PostgreSQL divergente em {table}.",
                diagnostic_code="SCHEMA_CONSTRAINT_MISMATCH",
                diagnostic_details={
                    "table": table,
                    "expected_counts": {
                        kind: count
                        for kind, count in expected.items()
                        if count
                    },
                    "actual_counts": actual_constraint_counts.get(table, {}),
                },
            )

    not_null_constraints = connection.execute(
        """
        SELECT c.relname, a.attname, con.convalidated
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL unnest(con.conkey) AS key(attnum)
        JOIN pg_attribute a
          ON a.attrelid = c.oid AND a.attnum = key.attnum
        WHERE n.nspname = 'public'
          AND con.contype = 'n'
          AND c.relname = ANY(%s)
        ORDER BY c.relname, a.attname
        """,
        (list(expected_tables),),
    ).fetchall()
    expected_not_null_columns = {
        (table, column)
        for table, metadata in expected_tables.items()
        for column, column_metadata in metadata["columns"].items()
        if not column_metadata["nullable"]
    }
    actual_not_null_columns = {
        (table, column)
        for table, column, _validated in not_null_constraints
    }
    not_null_column_counts = Counter(
        (table, column)
        for table, column, _validated in not_null_constraints
    )
    duplicate_not_null_columns = sorted(
        location
        for location, count in not_null_column_counts.items()
        if count != 1
    )
    unvalidated_not_null_columns = sorted(
        (table, column)
        for table, column, validated in not_null_constraints
        if not validated
    )
    if (
        actual_not_null_columns != expected_not_null_columns
        or len(not_null_constraints) != len(expected_not_null_columns)
        or duplicate_not_null_columns
        or unvalidated_not_null_columns
    ):
        raise PreflightError(
            "Constraints NOT NULL PostgreSQL divergentes.",
            diagnostic_code="SCHEMA_CONSTRAINT_MISMATCH",
            diagnostic_details={
                "constraint_type": "not_null",
                "expected_count": len(expected_not_null_columns),
                "actual_count": len(not_null_constraints),
                "missing_columns": sorted(
                    expected_not_null_columns - actual_not_null_columns
                ),
                "unexpected_columns": sorted(
                    actual_not_null_columns - expected_not_null_columns
                ),
                "duplicate_columns": duplicate_not_null_columns,
                "unvalidated_columns": unvalidated_not_null_columns,
            },
        )

    actual_indexes = {
        row[0]
        for row in connection.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
        ).fetchall()
    }
    missing_indexes = set(expected_indexes) - actual_indexes
    if missing_indexes:
        raise PreflightError(
            "Índices PostgreSQL ausentes.",
            diagnostic_code="SCHEMA_INDEX_MISMATCH",
            diagnostic_details={
                "missing_indexes": sorted(missing_indexes),
            },
        )
    return {
        "tables": len(expected_tables),
        "columns": sum(len(table["columns"]) for table in expected_tables.values()),
        "indexes": len(expected_indexes),
        "constraints_validated": True,
    }


def _source_owner_key_counts(connection, tables):
    results = {}
    for table, metadata in tables.items():
        if "owner_key" not in metadata["columns"]:
            continue
        rows = connection.execute(
            f"""
            SELECT owner_key, COUNT(*)
            FROM {_quote_identifier(table)}
            GROUP BY owner_key
            ORDER BY owner_key
            """
        ).fetchall()
        results[table] = {row[0]: row[1] for row in rows}
    return results


def _sqlite_transfer_schema(connection, postgres_tables):
    source_tables = {}
    for table in postgres_tables:
        rows = connection.execute(
            f"PRAGMA table_info({_quote_identifier(table)})"
        ).fetchall()
        primary_key = sorted(
            ((row["pk"], row["name"]) for row in rows if row["pk"]),
            key=lambda item: item[0],
        )
        source_tables[table] = {
            "columns": {
                row["name"]: {
                    "type": postgres_tables[table]["columns"][row["name"]][
                        "type"
                    ],
                    "nullable": postgres_tables[table]["columns"][row["name"]][
                        "nullable"
                    ],
                    "identity": postgres_tables[table]["columns"][row["name"]][
                        "identity"
                    ],
                }
                for row in rows
            },
            "primary_key": [name for _, name in primary_key],
            "foreign_keys": postgres_tables[table]["foreign_keys"],
            "check_constraints": postgres_tables[table]["check_constraints"],
            "unique_keys": postgres_tables[table]["unique_keys"],
        }
    return source_tables


def _validate_foreign_key_integrity(connection, tables):
    checked = 0
    for table, metadata in tables.items():
        for source_columns, referenced_table, referenced_columns, _action in (
            metadata["foreign_keys"]
        ):
            child = _quote_identifier(table)
            parent = _quote_identifier(referenced_table)
            non_null = " AND ".join(
                f"child.{_quote_identifier(column)} IS NOT NULL"
                for column in source_columns
            )
            matching = " AND ".join(
                f"parent.{_quote_identifier(parent_column)} = "
                f"child.{_quote_identifier(source_column)}"
                for source_column, parent_column in zip(
                    source_columns, referenced_columns
                )
            )
            count = connection.execute(
                f"SELECT COUNT(*) FROM {child} AS child "
                f"WHERE {non_null} AND NOT EXISTS ("
                f"SELECT 1 FROM {parent} AS parent WHERE {matching})"
            ).fetchone()[0]
            if count:
                raise PreflightError(
                    f"Chaves estrangeiras órfãs em {table} "
                    f"referenciando {referenced_table}."
                )
            checked += 1
    return {"foreign_keys_checked": checked, "orphan_rows": 0}


def _compare_owner_key_counts(source_counts, connection, tables):
    destination_counts = _source_owner_key_counts(connection, tables)
    if source_counts != destination_counts:
        raise PreflightError(
            "Distribuição de registros por owner_key divergiu após a carga."
        )
    return {
        table: {
            "distinct_owners": len(counts),
            "rows": sum(counts.values()),
        }
        for table, counts in source_counts.items()
    }


def _run_migration_stage(diagnostic_code, operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except Exception as error:
        raise PreflightError(
            "A etapa de migração foi interrompida.",
            diagnostic_code=diagnostic_code,
        ) from error


def create_verified_backup(sqlite_path, backup_root=BACKUP_ROOT):
    sqlite_path = Path(sqlite_path).resolve(strict=True)
    if backup_root is None:
        backup_root = BACKUP_ROOT
    backup_root = Path(backup_root).resolve()
    project_root = Path(__file__).resolve().parents[1]
    if backup_root == project_root or project_root in backup_root.parents:
        raise PreflightError("O backup precisa ficar fora da pasta do projeto.")
    backup_root.mkdir(parents=True, exist_ok=True)

    token = uuid.uuid4().hex
    partial_path = backup_root / f".sqlite-migration-{token}.partial"
    final_path = backup_root / f"sqlite-pre-migration-{token}.sqlite3"
    descriptor = os.open(
        partial_path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    os.close(descriptor)
    try:
        source = open_sqlite_readonly(sqlite_path)
        destination = sqlite3.connect(partial_path)
        try:
            source.backup(destination, pages=500, sleep=0.01)
            destination.commit()
        finally:
            destination.close()
            source.close()

        backup = open_sqlite_readonly(partial_path)
        try:
            integrity = [
                row[0] for row in backup.execute("PRAGMA integrity_check")
            ]
            foreign_key_errors = list(
                backup.execute("PRAGMA foreign_key_check")
            )
            if integrity != ["ok"] or foreign_key_errors:
                raise PreflightError(
                    "O backup SQLite não passou nas verificações de integridade."
                )
            backup_hash = _sha256_file(partial_path)
        finally:
            backup.close()
        partial_path.rename(final_path)
        return {
            "path": str(final_path),
            "sha256": backup_hash,
            "integrity_check": "ok",
            "foreign_key_errors": 0,
            "outside_project": True,
        }
    except Exception:
        if partial_path.exists():
            partial_path.unlink()
        raise


def _insert_source_data(source, connection, schema, table_order):
    counts = {}
    for table in table_order:
        metadata = schema[table]
        columns = list(metadata["columns"])
        quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        insert_sql = (
            f"INSERT INTO {_quote_identifier(table)} "
            f"({quoted_columns}) VALUES ({placeholders})"
        )
        select_columns = ", ".join(_quote_identifier(column) for column in columns)
        order = ", ".join(
            _quote_identifier(column) for column in metadata["primary_key"]
        )
        cursor = source.execute(
            f"SELECT {select_columns} FROM {_quote_identifier(table)} "
            f"ORDER BY {order}"
        )
        inserted = 0
        destination_cursor = connection.cursor()
        try:
            while True:
                batch = cursor.fetchmany(500)
                if not batch:
                    break
                values = [
                    tuple(
                        _adapt_value(
                            row[column],
                            metadata["columns"][column]["type"],
                        )
                        for column in columns
                    )
                    for row in batch
                ]
                destination_cursor.executemany(insert_sql, values)
                inserted += len(values)
        finally:
            destination_cursor.close()
        counts[table] = inserted
    return counts


def _restart_identity_sequences(connection, schema):
    restarted = {}
    for table, metadata in schema.items():
        for column, column_metadata in metadata["columns"].items():
            if column_metadata["identity"] is None:
                continue
            max_id = connection.execute(
                f"SELECT MAX({_quote_identifier(column)}) "
                f"FROM {_quote_identifier(table)}"
            ).fetchone()[0]
            next_value = 1 if max_id is None else int(max_id) + 1
            connection.execute(
                f"ALTER TABLE {_quote_identifier(table)} "
                f"ALTER COLUMN {_quote_identifier(column)} "
                f"RESTART WITH {next_value}"
            )
            restarted[f"{table}.{column}"] = next_value
    return restarted


def _apply_schema_migrations(connection, migrations_path, on_applied=None):
    connection.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied_versions = []
    files = sorted(Path(migrations_path).glob("[0-9][0-9][0-9]_*.sql"))
    if len(files) != 8:
        raise PreflightError(
            f"Esperadas oito migrações PostgreSQL; encontradas {len(files)}."
        )
    for migration_file in files:
        version_text = migration_file.name.split("_", maxsplit=1)[0]
        if not version_text.isdigit():
            raise PreflightError(
                f"Nome de migração inválido: {migration_file.name}."
            )
        version = int(version_text)
        connection.execute(migration_file.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migrations (version) VALUES (%s)",
            (version,),
        )
        applied_versions.append(version)
        if on_applied:
            on_applied(version)
    if applied_versions != list(range(1, 9)):
        raise PreflightError("As oito migrações não foram aplicadas em ordem.")
    return applied_versions


def diagnose_postgres_schema(
    connection,
    migrations_path=POSTGRES_MIGRATIONS_PATH,
):
    stage = "target_preflight"
    identity_confirmed = False
    destination_empty = False
    migration_attempted = False
    applied_versions = []
    validation = None
    diagnostic_code = None
    diagnostic_details = {}
    rollback_confirmed = False

    try:
        connection.rollback()
        connection.execute("BEGIN")
        destination = inspect_postgres_destination(
            connection,
            require_read_only=False,
        )
        identity_confirmed = True
        destination_empty = destination["empty"]
        stage = "schema_migration"
        migrations, indexes = _parse_postgres_migrations(migrations_path)
        migration_attempted = True
        applied_versions = _apply_schema_migrations(
            connection,
            migrations_path,
            on_applied=applied_versions.append,
        )
        stage = "schema_validation"
        try:
            validation = _validate_actual_postgres_schema(
                connection,
                migrations,
                indexes,
            )
        except PreflightError as error:
            diagnostic_code = (
                error.diagnostic_code
                if error.diagnostic_code
                != PreflightError.DEFAULT_DIAGNOSTIC_CODE
                else {
                    "target_preflight": "DIAGNOSTIC_TARGET_PREFLIGHT_FAILED",
                    "schema_migration": "DIAGNOSTIC_SCHEMA_MIGRATION_FAILED",
                    "schema_validation": "DIAGNOSTIC_SCHEMA_VALIDATION_FAILED",
                }.get(stage, "DIAGNOSTIC_FAILED")
            )
            diagnostic_details = error.diagnostic_details
    except PreflightError as error:
        diagnostic_code = (
            error.diagnostic_code
            if error.diagnostic_code
            != PreflightError.DEFAULT_DIAGNOSTIC_CODE
            else {
                "target_preflight": "DIAGNOSTIC_TARGET_PREFLIGHT_FAILED",
                "schema_migration": "DIAGNOSTIC_SCHEMA_MIGRATION_FAILED",
                "schema_validation": "DIAGNOSTIC_SCHEMA_VALIDATION_FAILED",
            }.get(stage, "DIAGNOSTIC_FAILED")
        )
        diagnostic_details = error.diagnostic_details
    except Exception:
        diagnostic_code = {
            "target_preflight": "DIAGNOSTIC_TARGET_PREFLIGHT_FAILED",
            "schema_migration": "DIAGNOSTIC_SCHEMA_MIGRATION_FAILED",
            "schema_validation": "DIAGNOSTIC_SCHEMA_VALIDATION_FAILED",
        }.get(stage, "DIAGNOSTIC_FAILED")
    finally:
        try:
            connection.rollback()
            rollback_confirmed = True
            if identity_confirmed and destination_empty and migration_attempted:
                try:
                    after_rollback = inspect_postgres_destination(
                        connection,
                        require_read_only=False,
                    )
                    rollback_confirmed = after_rollback["empty"]
                except Exception:
                    rollback_confirmed = False
                finally:
                    connection.rollback()
        except Exception:
            rollback_confirmed = False

    if not rollback_confirmed:
        diagnostic_code = "DIAGNOSTIC_ROLLBACK_NOT_CONFIRMED"
        diagnostic_details = {}

    return {
        "mode": "diagnose-local-schema",
        "status": (
            "aborted"
            if not rollback_confirmed
            else "validated"
            if validation is not None
            else "schema_mismatch"
            if diagnostic_code
            in {
                "SCHEMA_TABLES_MISMATCH",
                "SCHEMA_COLUMNS_MISMATCH",
                "SCHEMA_COLUMN_TYPE_MISMATCH",
                "SCHEMA_NULLABILITY_MISMATCH",
                "SCHEMA_IDENTITY_MISMATCH",
                "SCHEMA_DEFAULT_MISMATCH",
                "SCHEMA_CONSTRAINT_MISMATCH",
                "SCHEMA_INDEX_MISMATCH",
            }
            else "aborted"
        ),
        "diagnostic_code": diagnostic_code,
        "diagnostic_details": diagnostic_details,
        "migration_versions_applied_in_transaction": applied_versions,
        "data_transfer_started": False,
        "backup_created": False,
        "sqlite_accessed": False,
        "commit_executed": False,
        "rollback_confirmed": rollback_confirmed,
        "schema_validation": validation,
    }


def migrate_to_postgres(
    connection,
    sqlite_path=DEFAULT_SQLITE_PATH,
    backup_root=BACKUP_ROOT,
    migrations_path=POSTGRES_MIGRATIONS_PATH,
    expected_source_sha256=EXPECTED_SOURCE_SHA256,
    failure_hook=None,
):
    stage_code = "SOURCE_PREFLIGHT_FAILED"
    sqlite_path = Path(sqlite_path).resolve(strict=True)
    if sqlite_path != DEFAULT_SQLITE_PATH.resolve():
        raise PreflightError(
            "A transferência real aceita somente o SQLite original configurado.",
            diagnostic_code="SOURCE_PATH_NOT_AUTHORIZED",
        )
    source_hash = _sha256_file(sqlite_path)
    if expected_source_sha256 and source_hash != expected_source_sha256:
        raise PreflightError(
            "O SHA-256 do SQLite original não corresponde ao baseline autorizado.",
            diagnostic_code="SOURCE_HASH_MISMATCH",
        )

    original_preflight = _run_migration_stage(
        "SOURCE_PREFLIGHT_FAILED",
        run_preflight,
        sqlite_path,
        sqlite_only=True,
        postgres_migrations_path=migrations_path,
    )
    if not original_preflight["schema_comparison"]["matches"]:
        raise PreflightError(
            "O preflight do SQLite original encontrou divergências.",
            diagnostic_code="SOURCE_PREFLIGHT_FAILED",
        )

    stage_code = "DESTINATION_PREFLIGHT_FAILED"
    identity = _run_migration_stage(
        "DESTINATION_PREFLIGHT_FAILED",
        inspect_postgres_destination,
        connection,
        require_read_only=False,
    )
    if not identity["empty"]:
        raise PreflightError(
            "Destino PostgreSQL não está vazio; nenhuma escrita foi iniciada.",
            diagnostic_code="DESTINATION_NOT_EMPTY",
        )

    postgres_tables, postgres_indexes = _parse_postgres_migrations(
        migrations_path
    )
    stage_code = "SOURCE_CHECKSUM_FAILED"
    original = _run_migration_stage(
        "SOURCE_CHECKSUM_FAILED",
        open_sqlite_readonly,
        sqlite_path,
    )
    try:
        original_tables = _sqlite_transfer_schema(original, postgres_tables)
        original_counts = {
            table: _table_count(original, table)
            for table in original_tables
        }
        original_checksums = compute_canonical_checksums(
            original,
            original_tables,
        )
    finally:
        original.close()

    stage_code = "BACKUP_CREATION_FAILED"
    backup = _run_migration_stage(
        "BACKUP_CREATION_FAILED",
        create_verified_backup,
        sqlite_path,
        backup_root,
    )
    preflight = _run_migration_stage(
        "BACKUP_VALIDATION_FAILED",
        run_preflight,
        backup["path"],
        sqlite_only=True,
        postgres_migrations_path=migrations_path,
    )
    if not preflight["schema_comparison"]["matches"]:
        raise PreflightError(
            "O preflight da cópia de origem encontrou divergências.",
            diagnostic_code="BACKUP_VALIDATION_FAILED",
        )

    source = _run_migration_stage(
        "BACKUP_VALIDATION_FAILED",
        open_sqlite_readonly,
        backup["path"],
    )
    try:
        source_tables = _sqlite_transfer_schema(source, postgres_tables)
        table_order, _dependencies = _topological_order(postgres_tables)
        source_counts = {
            table: _table_count(source, table)
            for table in source_tables
        }
        source_checksums = compute_canonical_checksums(source, source_tables)
        _assert_table_counts_match(
            original_counts,
            source_counts,
            "o backup do SQLite",
        )
        _assert_checksums_match(
            original_checksums,
            source_checksums,
            "o backup do SQLite",
        )
        source_owner_counts = _source_owner_key_counts(source, source_tables)
    except Exception as error:
        source.close()
        raise PreflightError(
            "A validação do backup foi interrompida.",
            diagnostic_code="BACKUP_VALIDATION_FAILED",
        ) from error

    stage_code = "POSTGRES_SCHEMA_MIGRATION_FAILED"
    try:
        connection.rollback()
        connection.execute("BEGIN")
        connection.execute("SELECT pg_advisory_xact_lock(684371029154)")
        stage_code = "DESTINATION_RECHECK_FAILED"
        current_identity = inspect_postgres_destination(
            connection,
            require_read_only=False,
        )
        if not current_identity["empty"]:
            raise PreflightError(
                "O destino deixou de estar vazio antes da transação.",
                diagnostic_code="DESTINATION_NOT_EMPTY",
            )

        stage_code = "POSTGRES_SCHEMA_MIGRATION_FAILED"
        applied_versions = _apply_schema_migrations(connection, migrations_path)
        if failure_hook:
            failure_hook("after_schema")
        stage_code = "POSTGRES_SCHEMA_VALIDATION_FAILED"
        actual_schema = _validate_actual_postgres_schema(
            connection,
            postgres_tables,
            postgres_indexes,
        )
        stage_code = "DATA_TRANSFER_FAILED"
        inserted_counts = _insert_source_data(
            source,
            connection,
            source_tables,
            table_order,
        )
        _assert_table_counts_match(
            source_counts,
            inserted_counts,
            "a inserção PostgreSQL",
        )
        if failure_hook:
            failure_hook("after_data")

        stage_code = "POST_TRANSFER_VALIDATION_FAILED"
        destination_counts = {
            table: _table_count(connection, table)
            for table in source_tables
        }
        _assert_table_counts_match(
            source_counts,
            destination_counts,
            "a validação PostgreSQL",
        )
        foreign_key_report = _validate_foreign_key_integrity(
            connection,
            postgres_tables,
        )
        destination_checksums = compute_canonical_checksums(
            connection,
            source_tables,
            postgres=True,
        )
        _assert_checksums_match(
            source_checksums,
            destination_checksums,
            "a validação PostgreSQL",
        )
        owner_key_report = _compare_owner_key_counts(
            source_owner_counts,
            connection,
            source_tables,
        )
        stage_code = "IDENTITY_SEQUENCE_RESET_FAILED"
        identity_sequences = _restart_identity_sequences(
            connection,
            postgres_tables,
        )
        if failure_hook:
            failure_hook("after_sequences")

        stage_code = "POST_TRANSFER_VALIDATION_FAILED"
        if _sha256_file(sqlite_path) != source_hash:
            raise PreflightError(
                "O SHA-256 do SQLite original mudou durante a transferência.",
                diagnostic_code="SOURCE_CHANGED_DURING_MIGRATION",
            )
        connection.commit()
        return {
            "status": "committed",
            "source": {
                "path": str(sqlite_path),
                "sha256": source_hash,
                "backup": backup,
                "integrity_check": preflight["source"]["integrity_check"],
                "foreign_key_errors": preflight["source"]["foreign_key_errors"],
                "table_counts": source_counts,
                "total_rows": sum(source_counts.values()),
            },
            "destination": {
                "database": identity["database"],
                "user": identity["user"],
                "host": AUTHORIZED_HOST,
                "port": AUTHORIZED_PORT,
                "schema": "public",
                "migration_versions": applied_versions,
                "schema_validation": actual_schema,
                "table_counts": destination_counts,
                "foreign_key_integrity": foreign_key_report,
                "owner_key_isolation": owner_key_report,
            },
            "checksums": {
                "canonical": source_checksums,
                "equal": True,
                "historical_reference_hashes_replaced": False,
            },
            "identity_sequences_restarted": identity_sequences,
            "transaction": {
                "single_transaction": True,
                "committed": True,
                "rollback_on_any_failure": True,
            },
        }
    except Exception as error:
        connection.rollback()
        raise PreflightError(
            "A migração foi revertida antes do commit.",
            diagnostic_code=(
                error.diagnostic_code
                if isinstance(error, PreflightError)
                and error.diagnostic_code
                != PreflightError.DEFAULT_DIAGNOSTIC_CODE
                else stage_code
            ),
            diagnostic_details=(
                error.diagnostic_details
                if isinstance(error, PreflightError)
                else None
            ),
        ) from error
    finally:
        source.close()
