from pathlib import Path
import re

import pytest

from scripts import verify_postgres_migration as audit
from scripts.migration_errors import PreflightError


IDENTITY = (
    audit.AUTHORIZED_DATABASE,
    audit.AUTHORIZED_USER,
    audit.AUTHORIZED_HOST,
    audit.AUTHORIZED_PORT,
    "on",
    "public",
)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, counts=None, identity=IDENTITY):
        self.counts = dict(audit.EXPECTED_TABLE_COUNTS)
        if counts:
            self.counts.update(counts)
        self.identity = identity
        self.queries = []

    def execute(self, query, parameters=None):
        normalized = " ".join(query.split())
        self.queries.append(normalized)
        if "current_database()" in normalized:
            return FakeResult([self.identity])
        if "FROM information_schema.tables" in normalized:
            return FakeResult(
                [(table,) for table in audit.EXPECTED_TABLE_COUNTS]
                + [("schema_migrations",)]
            )
        if normalized.startswith("SELECT COUNT(*) FROM"):
            table = re.search(r'FROM "([^"]+)"', normalized).group(1)
            return FakeResult([(self.counts[table],)])
        if "FROM public.schema_migrations" in normalized:
            return FakeResult([(version,) for version in range(1, 9)])
        raise AssertionError(f"Consulta não simulada no teste: {normalized}")


def _expected_schema():
    tables = {}
    table_names = sorted(audit.EXPECTED_TABLE_COUNTS)
    identities_left = audit.EXPECTED_IDENTITY_COUNT
    for table in table_names:
        identity = identities_left > 0
        identities_left -= int(identity)
        tables[table] = {
            "columns": {
                "id": {
                    "type": "bigint",
                    "nullable": False,
                    "identity": "BY DEFAULT" if identity else None,
                    "default": None,
                }
            },
            "primary_key": ["id"],
            "unique_keys": set(),
            "check_constraints": [],
            "foreign_keys": [],
        }
    children = table_names[:audit.EXPECTED_FOREIGN_KEY_COUNT]
    for index in range(audit.EXPECTED_FOREIGN_KEY_COUNT):
        child = children[index % len(children)]
        parent = table_names[
            (index + 2 + index // len(table_names)) % len(table_names)
        ]
        tables[child]["foreign_keys"].append(
            (("id",), parent, ("id",), "NO ACTION")
        )
    return tables


def _run_successful_audit(monkeypatch, connection):
    expected_tables = _expected_schema()
    monkeypatch.setattr(
        audit,
        "_parse_postgres_migrations",
        lambda _path: (expected_tables, {}),
    )
    monkeypatch.setattr(
        audit,
        "_validate_actual_postgres_schema",
        lambda *_args: {"tables": 17},
    )
    monkeypatch.setattr(audit, "_schema_inventory", lambda *_args: {})
    monkeypatch.setattr(audit, "_actual_foreign_keys", lambda *_args: [])
    monkeypatch.setattr(
        audit,
        "_compare_foreign_keys",
        lambda *_args: {
            "matches": True,
            "expected_count": 18,
            "actual_count": 18,
            "missing": [],
            "unexpected": [],
            "unvalidated": [],
        },
    )
    monkeypatch.setattr(
        audit,
        "_check_foreign_key_integrity",
        lambda *_args: {
            "foreign_keys_checked": 18,
            "orphan_rows": 0,
            "orphans": [],
            "matches": True,
        },
    )
    monkeypatch.setattr(
        audit,
        "_identity_sequence_state",
        lambda *_args: {
            "identity_column_count": 12,
            "sequences": {},
            "matches": True,
        },
    )
    monkeypatch.setattr(
        audit,
        "_owner_distribution",
        lambda *_args: {
            "favorites": {
                "distinct_owners": 1,
                "rows": 1,
                "rows_per_owner": [{"owner": "owner-1", "rows": 1}],
            }
        },
    )
    owner_key_counts = {"favorites": {"synthetic-owner": 1}}
    monkeypatch.setattr(
        audit,
        "_source_owner_key_counts",
        lambda *_args: owner_key_counts,
    )
    monkeypatch.setattr(
        audit,
        "_sqlite_owner_key_counts",
        lambda *_args: owner_key_counts,
    )
    checksums = {
        "format": "palavra-do-dia-content-v1",
        "groups": {
            group: {"sha256": f"{group}-digest"}
            for group in ("bible", "editorial", "personal")
        },
    }
    monkeypatch.setattr(audit, "_sqlite_content_checksums", lambda *_args: checksums)
    monkeypatch.setattr(
        audit,
        "compute_canonical_checksums",
        lambda *_args, **_kwargs: checksums,
    )
    monkeypatch.setattr(
        audit,
        "_local_file_checks",
        lambda path, expected: {
            "path": str(path),
            "exists": True,
            "sha256": expected,
            "expected_sha256": expected,
            "matches_expected_sha256": True,
        },
    )
    monkeypatch.setattr(
        audit,
        "_backup_file_checks",
        lambda *_args: {"matching_backup_count": 1, "files": []},
    )
    return audit.run_postgres_audit(
        connection,
        sqlite_path=Path("source.sqlite3"),
        backup_root=Path("backups"),
        postgres_migrations_path=Path("migrations"),
    )


def test_read_only_audit_uses_only_select_queries(monkeypatch):
    connection = FakeConnection()
    report = _run_successful_audit(monkeypatch, connection)

    assert report["status"] == "passed"
    assert report["checks"]["identity"]
    assert report["checks"]["table_counts"]
    assert report["checks"]["foreign_keys"]
    assert report["connection"]["read_only"]
    assert all(query.lstrip().upper().startswith("SELECT") for query in connection.queries)
    assert "owner-1" in str(report["owner_key_distribution"])
    assert "visitor" not in str(report["owner_key_distribution"])


def test_wrong_database_identity_stops_before_other_queries():
    wrong_identity = (
        "not_the_test_database",
        *IDENTITY[1:],
    )
    connection = FakeConnection(identity=wrong_identity)

    with pytest.raises(PreflightError) as error:
        audit.run_postgres_audit(connection)

    assert error.value.diagnostic_code == "AUDIT_TARGET_IDENTITY_MISMATCH"
    assert len(connection.queries) == 1


def test_read_write_connection_is_rejected():
    identity = (*IDENTITY[:4], "off", IDENTITY[5])
    connection = FakeConnection(identity=identity)

    with pytest.raises(PreflightError) as error:
        audit._confirm_identity(connection)

    assert error.value.diagnostic_code == "AUDIT_READ_ONLY_REQUIRED"
    assert len(connection.queries) == 1


def test_count_comparison_reports_table_divergence():
    actual = dict(audit.EXPECTED_TABLE_COUNTS)
    actual["verses"] -= 1

    comparison = audit.compare_table_counts(
        audit.EXPECTED_TABLE_COUNTS,
        actual,
    )

    assert not comparison["matches"]
    assert comparison["differences"]["verses"] == {
        "expected": 31102,
        "actual": 31101,
    }


def test_checksum_comparison_reports_group_divergence():
    expected = {
        "groups": {
            "bible": {"sha256": "source-hash"},
            "editorial": {"sha256": "same-hash"},
        }
    }
    actual = {
        "groups": {
            "bible": {"sha256": "different-hash"},
            "editorial": {"sha256": "same-hash"},
        }
    }

    comparison = audit.compare_canonical_checksums(expected, actual)

    assert not comparison["matches"]
    assert not comparison["groups"]["bible"]["matches"]
    assert comparison["groups"]["editorial"]["matches"]


@pytest.mark.parametrize(
    ("last_value", "is_called", "maximum", "expected_safe"),
    [(11, False, 10, True), (10, True, 10, True), (9, True, 10, False)],
)
def test_identity_sequence_inspection_is_read_only(
    last_value,
    is_called,
    maximum,
    expected_safe,
):
    class SequenceConnection:
        def __init__(self):
            self.queries = []

        def execute(self, query, parameters=None):
            normalized = " ".join(query.split())
            self.queries.append(normalized)
            if "pg_get_serial_sequence" in normalized:
                return FakeResult([("public.foo_id_seq",)])
            if "relation.oid = %s::regclass" in normalized:
                return FakeResult([("public", "foo_id_seq")])
            if "SELECT last_value, is_called" in normalized:
                return FakeResult([(last_value, is_called)])
            if "SELECT MAX(" in normalized:
                return FakeResult([(maximum,)])
            raise AssertionError(f"Consulta não simulada: {normalized}")

    connection = SequenceConnection()
    tables = {
        "foo": {
            "columns": {
                "id": {
                    "identity": "BY DEFAULT",
                }
            }
        }
    }

    report = audit._identity_sequence_state(connection, tables)

    assert report["sequences"]["foo.id"]["next_value_is_safe"] is expected_safe
    assert all(
        query.upper().startswith("SELECT")
        for query in connection.queries
    )
