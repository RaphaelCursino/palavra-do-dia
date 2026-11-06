import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.migration_errors import PreflightError
from scripts.migrate_sqlite_to_postgres import (
    DEFAULT_SQLITE_PATH,
    POSTGRES_MIGRATIONS_PATH,
    _parse_postgres_migrations,
    _quote_identifier,
    open_sqlite_readonly,
)
from scripts.sqlite_postgres_transfer import (
    AUTHORIZED_DATABASE,
    AUTHORIZED_HOST,
    AUTHORIZED_PORT,
    AUTHORIZED_USER,
    BACKUP_ROOT,
    EXPECTED_SOURCE_SHA256,
    _source_owner_key_counts,
    _sqlite_transfer_schema,
    _table_count,
    _validate_actual_postgres_schema,
    compute_canonical_checksums,
)


EXPECTED_BACKUP_SHA256 = (
    "541739059cc089bac55b042c407f264cf063d97cf5f8712cc8a04b39a9e0a1f3"
)
EXPECTED_TABLE_COUNTS = {
    "achievement_unlocks": 0,
    "books": 66,
    "chapters": 1189,
    "daily_readings": 7,
    "favorites": 0,
    "import_batches": 1,
    "prayer_entries": 0,
    "reader_preferences": 1,
    "reading_completions": 0,
    "reading_goals": 1,
    "reflections": 7,
    "translations": 1,
    "verse_annotations": 0,
    "verse_highlights": 0,
    "verse_notes": 1290,
    "verses": 31102,
    "weekly_goal_achievements": 0,
}
EXPECTED_TOTAL_ROWS = 33665
EXPECTED_MIGRATION_VERSIONS = list(range(1, 9))
EXPECTED_FOREIGN_KEY_COUNT = 18
EXPECTED_IDENTITY_COUNT = 12


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare_table_counts(expected, actual):
    differences = {
        table: {
            "expected": expected.get(table),
            "actual": actual.get(table),
        }
        for table in sorted(set(expected) | set(actual))
        if expected.get(table) != actual.get(table)
    }
    return {
        "matches": not differences,
        "differences": differences,
        "expected_total": sum(expected.values()),
        "actual_total": sum(actual.values()),
    }


def compare_canonical_checksums(expected, actual):
    expected_groups = expected.get("groups", {})
    actual_groups = actual.get("groups", {})
    groups = sorted(set(expected_groups) | set(actual_groups))
    comparison = {
        group: {
            "matches": (
                expected_groups.get(group, {}).get("sha256")
                == actual_groups.get(group, {}).get("sha256")
            ),
            "expected_sha256": expected_groups.get(group, {}).get("sha256"),
            "actual_sha256": actual_groups.get(group, {}).get("sha256"),
        }
        for group in groups
    }
    expected_tables = expected.get("tables", {})
    actual_tables = actual.get("tables", {})
    tables = sorted(set(expected_tables) | set(actual_tables))
    table_comparison = {
        table: {
            "matches": expected_tables.get(table) == actual_tables.get(table),
            "expected_sha256": expected_tables.get(table),
            "actual_sha256": actual_tables.get(table),
        }
        for table in tables
    }
    return {
        "matches": all(item["matches"] for item in comparison.values())
        and set(expected_groups) == set(actual_groups)
        and all(item["matches"] for item in table_comparison.values())
        and set(expected_tables) == set(actual_tables),
        "groups": comparison,
        "tables": table_comparison,
    }


def _confirm_identity(connection):
    row = connection.execute(
        """
        SELECT current_database(), current_user,
               inet_server_addr()::text, inet_server_port(),
               current_setting('transaction_read_only'),
               current_schema()
        """
    ).fetchone()
    try:
        server_ip = str(ipaddress.ip_interface(row[2]).ip)
    except (TypeError, ValueError) as error:
        raise PreflightError(
            "A identidade do PostgreSQL não pôde ser confirmada.",
            diagnostic_code="AUDIT_TARGET_IDENTITY_MISMATCH",
        ) from error
    identity_matches = (
        row[0] == AUTHORIZED_DATABASE
        and row[1] == AUTHORIZED_USER
        and server_ip == AUTHORIZED_HOST
        and row[3] == AUTHORIZED_PORT
        and row[5] == "public"
    )
    if not identity_matches:
        raise PreflightError(
            "Auditoria recusada: destino não corresponde ao alvo autorizado.",
            diagnostic_code="AUDIT_TARGET_IDENTITY_MISMATCH",
            diagnostic_details={
                "database_matches": row[0] == AUTHORIZED_DATABASE,
                "user_matches": row[1] == AUTHORIZED_USER,
                "host_matches": server_ip == AUTHORIZED_HOST,
                "port_matches": row[3] == AUTHORIZED_PORT,
                "schema_matches": row[5] == "public",
            },
        )
    if row[4] != "on":
        raise PreflightError(
            "A conexão não está configurada como somente leitura.",
            diagnostic_code="AUDIT_READ_ONLY_REQUIRED",
        )
    return {
        "database": row[0],
        "user": row[1],
        "host": server_ip,
        "port": row[3],
        "schema": row[5],
        "read_only": True,
    }


def _actual_foreign_keys(connection):
    rows = connection.execute(
        """
        SELECT child.relname,
               ARRAY(
                   SELECT attribute.attname
                   FROM unnest(constraint_row.conkey)
                        WITH ORDINALITY AS child_key(attnum, position)
                   JOIN pg_attribute attribute
                     ON attribute.attrelid = child.oid
                    AND attribute.attnum = child_key.attnum
                   ORDER BY child_key.position
               ),
               parent.relname,
               ARRAY(
                   SELECT attribute.attname
                   FROM unnest(constraint_row.confkey)
                        WITH ORDINALITY AS parent_key(attnum, position)
                   JOIN pg_attribute attribute
                     ON attribute.attrelid = parent.oid
                    AND attribute.attnum = parent_key.attnum
                   ORDER BY parent_key.position
               ),
               constraint_row.confdeltype,
               constraint_row.convalidated
        FROM pg_constraint constraint_row
        JOIN pg_class child ON child.oid = constraint_row.conrelid
        JOIN pg_namespace child_namespace
          ON child_namespace.oid = child.relnamespace
        JOIN pg_class parent ON parent.oid = constraint_row.confrelid
        JOIN pg_namespace parent_namespace
          ON parent_namespace.oid = parent.relnamespace
        WHERE child_namespace.nspname = 'public'
          AND parent_namespace.nspname = 'public'
          AND constraint_row.contype = 'f'
        ORDER BY child.relname, constraint_row.conname
        """
    ).fetchall()
    delete_actions = {
        "a": "NO ACTION",
        "r": "RESTRICT",
        "c": "CASCADE",
        "n": "SET NULL",
        "d": "SET DEFAULT",
    }
    return [
        (
            table,
            tuple(columns),
            referenced_table,
            tuple(referenced_columns),
            delete_actions[action],
            validated,
        )
        for (
            table,
            columns,
            referenced_table,
            referenced_columns,
            action,
            validated,
        ) in rows
    ]


def _compare_foreign_keys(expected_tables, actual_rows):
    expected = {
        (table, tuple(columns), referenced, tuple(ref_columns), action)
        for table, metadata in expected_tables.items()
        for columns, referenced, ref_columns, action in metadata["foreign_keys"]
    }
    actual = {
        (table, columns, referenced, ref_columns, action)
        for (
            table,
            columns,
            referenced,
            ref_columns,
            action,
            _validated,
        ) in actual_rows
    }
    unvalidated = [
        (table, columns)
        for table, columns, _ref, _ref_columns, _action, validated in actual_rows
        if not validated
    ]
    return {
        "expected_count": len(expected),
        "actual_count": len(actual_rows),
        "matches": expected == actual
        and len(actual_rows) == len(expected)
        and not unvalidated,
        "missing": sorted(expected - actual),
        "unexpected": sorted(actual - expected),
        "unvalidated": sorted(unvalidated),
    }


def _check_foreign_key_integrity(connection, tables):
    checked = 0
    orphans = []
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
            orphan_count = connection.execute(
                f"SELECT COUNT(*) FROM {child} AS child "
                f"WHERE {non_null} AND NOT EXISTS ("
                f"SELECT 1 FROM {parent} AS parent WHERE {matching})"
            ).fetchone()[0]
            if orphan_count:
                orphans.append(
                    {
                        "table": table,
                        "columns": list(source_columns),
                        "referenced_table": referenced_table,
                        "count": orphan_count,
                    }
                )
            checked += 1
    return {
        "foreign_keys_checked": checked,
        "orphan_rows": sum(item["count"] for item in orphans),
        "orphans": orphans,
        "matches": (
            checked == EXPECTED_FOREIGN_KEY_COUNT and not orphans
        ),
    }


def _schema_inventory(connection, expected_tables):
    inspected_tables = [*expected_tables, "schema_migrations"]
    columns = connection.execute(
        """
        SELECT table_name, column_name, data_type, is_nullable,
               is_identity, identity_generation, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = ANY(%s)
        ORDER BY table_name, ordinal_position
        """,
        (inspected_tables,),
    ).fetchall()
    constraints = connection.execute(
        """
        SELECT relation.relname, constraint_row.contype,
               COUNT(*), BOOL_AND(constraint_row.convalidated)
        FROM pg_constraint constraint_row
        JOIN pg_class relation ON relation.oid = constraint_row.conrelid
        JOIN pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname = ANY(%s)
        GROUP BY relation.relname, constraint_row.contype
        ORDER BY relation.relname, constraint_row.contype
        """,
        (inspected_tables,),
    ).fetchall()
    indexes = connection.execute(
        """
        SELECT tablename, indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
        ORDER BY tablename, indexname
        """
    ).fetchall()
    not_null_rows = connection.execute(
        """
        SELECT relation.relname, attribute.attname, constraint_row.convalidated
        FROM pg_constraint constraint_row
        JOIN pg_class relation ON relation.oid = constraint_row.conrelid
        JOIN pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        CROSS JOIN LATERAL unnest(constraint_row.conkey)
             AS key(attnum)
        JOIN pg_attribute attribute
          ON attribute.attrelid = relation.oid
         AND attribute.attnum = key.attnum
        WHERE namespace.nspname = 'public'
          AND constraint_row.contype = 'n'
          AND relation.relname = ANY(%s)
        ORDER BY relation.relname, attribute.attname
        """,
        (inspected_tables,),
    ).fetchall()
    return {
        "columns": [
            {
                "table": row[0],
                "column": row[1],
                "type": row[2],
                "nullable": row[3] == "YES",
                "identity": row[5] if row[4] == "YES" else None,
                "default_present": row[6] is not None,
            }
            for row in columns
        ],
        "constraint_counts": [
            {
                "table": row[0],
                "type": row[1],
                "count": row[2],
                "all_validated": row[3],
            }
            for row in constraints
        ],
        "not_null_columns": [
            {
                "table": row[0],
                "column": row[1],
                "validated": row[2],
            }
            for row in not_null_rows
        ],
        "indexes": [
            {"table": row[0], "name": row[1], "definition": row[2]}
            for row in indexes
        ],
        "control_table_included": "schema_migrations",
    }


def _owner_distribution(owner_counts):
    report = {}
    for table, counts in owner_counts.items():
        ordered_counts = sorted(counts.values())
        report[table] = {
            "distinct_owners": len(counts),
            "rows": sum(counts.values()),
            "rows_per_owner": [
                {"owner": f"owner-{index + 1}", "rows": count}
                for index, count in enumerate(ordered_counts)
            ],
        }
    return report


def _sqlite_owner_key_counts(path, tables):
    connection = open_sqlite_readonly(path)
    try:
        return _source_owner_key_counts(connection, tables)
    finally:
        connection.close()


def _identity_sequence_state(connection, tables):
    identity_columns = [
        (table, column)
        for table, metadata in tables.items()
        for column, details in metadata["columns"].items()
        if details["identity"]
    ]
    sequences = {}
    for table, column in identity_columns:
        qualified_table = f"public.{table}"
        sequence = connection.execute(
            "SELECT pg_get_serial_sequence(%s, %s)",
            (qualified_table, column),
        ).fetchone()[0]
        if not sequence:
            sequences[f"{table}.{column}"] = {
                "found": False,
                "next_value_is_safe": False,
            }
            continue
        schema_name, sequence_name = connection.execute(
            """
            SELECT namespace.nspname, relation.relname
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE relation.oid = %s::regclass
            """,
            (sequence,),
        ).fetchone()
        sequence_state = connection.execute(
            f"SELECT last_value, is_called "
            f"FROM {_quote_identifier(schema_name)}."
            f"{_quote_identifier(sequence_name)}"
        ).fetchone()
        maximum = connection.execute(
            f"SELECT MAX({_quote_identifier(column)}) "
            f"FROM {_quote_identifier(table)}"
        ).fetchone()[0]
        expected_next = 1 if maximum is None else int(maximum) + 1
        last_value, is_called = sequence_state
        next_value = int(last_value) + (1 if is_called else 0)
        next_value_is_safe = (
            next_value >= 1
            if maximum is None
            else next_value > int(maximum)
        )
        sequences[f"{table}.{column}"] = {
            "found": True,
            "schema": schema_name,
            "max_id": maximum,
            "last_value": int(last_value),
            "is_called": bool(is_called),
            "next_value": next_value,
            "expected_next_value": expected_next,
            "next_value_is_safe": (
                schema_name == "public" and next_value_is_safe
            ),
        }
    return {
        "identity_column_count": len(identity_columns),
        "sequences": sequences,
        "matches": all(
            sequence["found"] and sequence["next_value_is_safe"]
            for sequence in sequences.values()
        ),
    }


def _sqlite_content_checksums(path, expected_tables):
    connection = sqlite3.connect(
        f"{Path(path).resolve(strict=True).as_uri()}?mode=ro",
        uri=True,
        timeout=5,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        schema = _sqlite_transfer_schema(connection, expected_tables)
        return compute_canonical_checksums(connection, schema)
    finally:
        connection.close()


def _local_file_checks(path, expected_hash):
    try:
        actual_hash = _sha256_file(path)
    except OSError:
        return {
            "path": str(path),
            "exists": False,
            "matches_expected_sha256": False,
        }
    return {
        "path": str(path),
        "exists": True,
        "sha256": actual_hash,
        "expected_sha256": expected_hash,
        "matches_expected_sha256": actual_hash == expected_hash,
    }


def _backup_file_checks(backup_root, expected_hash):
    candidates = sorted(Path(backup_root).glob("sqlite-pre-migration-*.sqlite3"))
    files = [_local_file_checks(path, expected_hash) for path in candidates]
    return {
        "expected_sha256": expected_hash,
        "candidate_count": len(files),
        "matching_backup_count": sum(
            item.get("matches_expected_sha256", False) for item in files
        ),
        "files": files,
    }


def run_postgres_audit(
    connection,
    sqlite_path=DEFAULT_SQLITE_PATH,
    backup_root=BACKUP_ROOT,
    postgres_migrations_path=POSTGRES_MIGRATIONS_PATH,
):
    identity = _confirm_identity(connection)
    expected_tables, expected_indexes = _parse_postgres_migrations(
        postgres_migrations_path
    )
    table_names = sorted(expected_tables)
    expected_foreign_keys = sum(
        len(metadata["foreign_keys"])
        for metadata in expected_tables.values()
    )
    expected_identities = sum(
        bool(column["identity"])
        for metadata in expected_tables.values()
        for column in metadata["columns"].values()
    )
    if (
        len(table_names) != 17
        or expected_foreign_keys != EXPECTED_FOREIGN_KEY_COUNT
        or expected_identities != EXPECTED_IDENTITY_COUNT
    ):
        raise PreflightError(
            "O manifesto PostgreSQL não corresponde à estrutura esperada.",
            diagnostic_code="AUDIT_EXPECTED_SCHEMA_INVALID",
        )

    public_table_names = {
        row[0]
        for row in connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        ).fetchall()
    }
    control_table_exists = "schema_migrations" in public_table_names
    actual_table_names = public_table_names - {"schema_migrations"}
    table_existence = {
        table: table in actual_table_names for table in table_names
    }
    actual_counts = {}
    for table in table_names:
        if table_existence[table]:
            actual_counts[table] = _table_count(connection, table)
    counts = compare_table_counts(EXPECTED_TABLE_COUNTS, actual_counts)
    counts["expected_total"] = EXPECTED_TOTAL_ROWS
    counts["actual_total"] = sum(actual_counts.values())
    counts["matches"] = (
        counts["matches"]
        and counts["expected_total"] == EXPECTED_TOTAL_ROWS
        and counts["actual_total"] == EXPECTED_TOTAL_ROWS
        and len(actual_table_names) == 17
    )

    migration_versions = []
    if control_table_exists:
        migration_versions = [
            row[0]
            for row in connection.execute(
                """
                SELECT version FROM public.schema_migrations
                ORDER BY version
                """
            ).fetchall()
        ]
    migration_check = {
        "table_exists": control_table_exists,
        "expected_versions": EXPECTED_MIGRATION_VERSIONS,
        "actual_versions": migration_versions,
        "matches": (
            control_table_exists
            and migration_versions == EXPECTED_MIGRATION_VERSIONS
        ),
    }

    try:
        schema_validation = _validate_actual_postgres_schema(
            connection,
            expected_tables,
            expected_indexes,
        )
        schema_check = {
            "matches": True,
            "details": schema_validation,
            "diagnostic_code": None,
            "diagnostic_details": {},
        }
    except PreflightError as error:
        schema_check = {
            "matches": False,
            "details": None,
            "diagnostic_code": error.diagnostic_code,
            "diagnostic_details": error.diagnostic_details,
        }
    schema_check["inventory"] = _schema_inventory(
        connection,
        expected_tables,
    )
    schema_check["expected_columns"] = [
        {
            "table": table,
            "column": column,
            "type": details["type"],
            "nullable": details["nullable"],
            "identity": details["identity"],
            "default_present": details["default"] is not None,
        }
        for table, metadata in expected_tables.items()
        for column, details in metadata["columns"].items()
    ]
    schema_check["expected_constraints_by_table"] = {
        table: {
            "p": int(bool(metadata["primary_key"])),
            "f": len(metadata["foreign_keys"]),
            "u": len(metadata["unique_keys"]),
            "c": len(metadata["check_constraints"]),
            "n": sum(
                not column["nullable"]
                for column in metadata["columns"].values()
            ),
        }
        for table, metadata in expected_tables.items()
    }
    schema_check["expected_indexes"] = sorted(expected_indexes)

    source_hash = _local_file_checks(sqlite_path, EXPECTED_SOURCE_SHA256)
    backup_files = _backup_file_checks(backup_root, EXPECTED_BACKUP_SHA256)
    if schema_check["matches"]:
        actual_foreign_keys = _actual_foreign_keys(connection)
        foreign_key_comparison = _compare_foreign_keys(
            expected_tables,
            actual_foreign_keys,
        )
        orphan_check = _check_foreign_key_integrity(
            connection,
            expected_tables,
        )
        foreign_key_comparison["orphan_rows"] = orphan_check["orphan_rows"]
        foreign_key_comparison["orphan_details"] = orphan_check["orphans"]
        foreign_key_comparison["integrity_matches"] = (
            foreign_key_comparison["matches"]
            and orphan_check["matches"]
        )
        identities = _identity_sequence_state(connection, expected_tables)
    else:
        foreign_key_comparison = {
            "expected_count": EXPECTED_FOREIGN_KEY_COUNT,
            "actual_count": None,
            "matches": False,
            "integrity_matches": False,
            "skipped": True,
            "reason": "schema_validation_failed",
        }
        identities = {
            "identity_column_count": EXPECTED_IDENTITY_COUNT,
            "sequences": {},
            "matches": False,
            "skipped": True,
            "reason": "schema_validation_failed",
        }

    if schema_check["matches"] and source_hash["exists"]:
        postgres_owner_counts = _source_owner_key_counts(
            connection,
            expected_tables,
        )
        source_owner_counts = _sqlite_owner_key_counts(
            sqlite_path,
            expected_tables,
        )
        owner_key_comparison = {
            "matches": source_owner_counts == postgres_owner_counts,
            "sqlite": _owner_distribution(source_owner_counts),
            "postgresql": _owner_distribution(postgres_owner_counts),
        }
        source_checksums = _sqlite_content_checksums(
            sqlite_path,
            expected_tables,
        )
        postgres_schema = {
            table: {
                "columns": {
                    column: {
                        "type": metadata["type"],
                        "nullable": metadata["nullable"],
                        "identity": metadata["identity"],
                    }
                    for column, metadata in table_metadata["columns"].items()
                },
                "primary_key": table_metadata["primary_key"],
            }
            for table, table_metadata in expected_tables.items()
        }
        postgres_checksums = compute_canonical_checksums(
            connection,
            postgres_schema,
            postgres=True,
        )
        checksum_comparison = compare_canonical_checksums(
            source_checksums,
            postgres_checksums,
        )
    else:
        owner_key_comparison = {
            "matches": False,
            "skipped": True,
            "reason": (
                "schema_validation_failed"
                if not schema_check["matches"]
                else "sqlite_source_unavailable"
            ),
        }
        source_checksums = {}
        postgres_checksums = {}
        checksum_comparison = {
            "matches": False,
            "groups": {},
            "tables": {},
            "skipped": True,
            "reason": owner_key_comparison["reason"],
        }

    checks = {
        "identity": True,
        "application_tables": (
            len(actual_table_names) == 17
            and all(table_existence.values())
        ),
        "table_counts": counts["matches"],
        "migration_versions": migration_check["matches"],
        "schema": schema_check["matches"],
        "foreign_keys": foreign_key_comparison["integrity_matches"],
        "identity_sequences": identities["matches"],
        "owner_key_distribution": owner_key_comparison["matches"],
        "canonical_checksums": checksum_comparison["matches"],
        "sqlite_sha256": source_hash["matches_expected_sha256"],
        "backup_sha256": backup_files["matching_backup_count"] > 0,
    }
    return {
        "mode": "read-only-postgres-migration-audit",
        "status": "passed" if all(checks.values()) else "mismatches_found",
        "connection": identity,
        "checks": checks,
        "application_tables": {
            "expected_count": 17,
            "actual_count": len(actual_table_names),
            "existence": table_existence,
        },
        "table_counts": counts,
        "migration_versions": migration_check,
        "schema": schema_check,
        "foreign_keys": foreign_key_comparison,
        "identity_sequences": identities,
        "identity_sequences_expected": EXPECTED_IDENTITY_COUNT,
        "owner_key_distribution": owner_key_comparison,
        "canonical_checksums": {
            "format": "palavra-do-dia-content-v1",
            "historical_hashes_changed": False,
            "comparison": checksum_comparison,
            "sqlite": source_checksums,
            "postgresql": postgres_checksums,
        },
        "source_sqlite": source_hash,
        "backups": backup_files,
        "read_only": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Auditoria somente leitura da migração PostgreSQL local."
    )
    parser.parse_args(argv)
    try:
        import getpass

        import psycopg

        if not sys.stdin.isatty():
            raise PreflightError(
                "A senha só pode ser solicitada em terminal interativo "
                "com entrada oculta.",
                diagnostic_code="AUDIT_INTERACTIVE_TERMINAL_REQUIRED",
            )
        password = getpass.getpass(
            "Senha do PostgreSQL local (entrada oculta): "
        )
        if not password:
            raise PreflightError(
                "Senha vazia; auditoria cancelada.",
                diagnostic_code="AUDIT_PASSWORD_REQUIRED",
            )
        try:
            connection = psycopg.connect(
                host=AUTHORIZED_HOST,
                port=AUTHORIZED_PORT,
                dbname=AUTHORIZED_DATABASE,
                user=AUTHORIZED_USER,
                password=password,
                connect_timeout=5,
                options="-c default_transaction_read_only=on",
            )
        except Exception as error:
            raise PreflightError(
                "A conexão com o PostgreSQL local falhou.",
                diagnostic_code="AUDIT_POSTGRES_CONNECTION_FAILED",
            ) from error
        finally:
            del password

        try:
            report = run_postgres_audit(connection)
        finally:
            connection.rollback()
            connection.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" else 1
    except PreflightError as error:
        safe_details = error.diagnostic_details
        suffix = (
            " details="
            + json.dumps(safe_details, sort_keys=True, separators=(",", ":"))
            if safe_details
            else ""
        )
        print(
            f"Auditoria interrompida; code={error.diagnostic_code}{suffix}.",
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            "Auditoria interrompida; code=AUDIT_UNEXPECTED_FAILURE.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
