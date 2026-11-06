from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from app.db import POSTGRES_MIGRATIONS_PATH, apply_postgres_migrations


logger = logging.getLogger(__name__)

TEST_DATABASE_NAME = "palavra_dia_pytest"
TEST_DATABASE_HOST = "127.0.0.1"
TEST_DATABASE_PORT = 5432
TEST_DATABASE_URL_ENV = "PALAVRA_DO_DIA_TEST_DATABASE_URL"
LOOPBACK_ACKNOWLEDGEMENT_ENV = (
    "PALAVRA_DO_DIA_TEST_ACKNOWLEDGE_LOOPBACK_TUNNEL_LIMITATION"
)
LOOPBACK_ACKNOWLEDGEMENT_VALUE = "I_ACKNOWLEDGE_LOOPBACK_IS_NOT_LOCALITY_PROOF"

_POSTGRES_TEST_OBJECTS_QUERY = """
    SELECT object_kind, object_schema, object_name
    FROM (
        SELECT
            'schema' AS object_kind,
            namespace.nspname AS object_schema,
            namespace.nspname AS object_name
        FROM pg_namespace AS namespace
        WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema', 'public')
          AND namespace.nspname <> 'pg_toast'
          AND namespace.nspname <> (
              'pg_toast_' ||
              (SELECT oid::text FROM pg_database
               WHERE datname = current_database())
          )
        UNION ALL
        SELECT
            CASE
                WHEN relation.relkind IN ('r', 'p', 'v', 'm', 'f')
                    THEN 'table'
                WHEN relation.relkind = 'S' THEN 'sequence'
                WHEN relation.relkind IN ('i', 'I') THEN 'index'
                ELSE 'relation'
            END,
            namespace.nspname,
            relation.relname
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f', 'S', 'i', 'I', 'c')
        UNION ALL
        SELECT
            'constraint',
            namespace.nspname,
            relation.relname || ':' || pgc.contype::text || ':' ||
                COUNT(*)::text
        FROM pg_constraint AS pgc
        JOIN pg_class AS relation
          ON relation.oid = pgc.conrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND pgc.contype IN ('p', 'u', 'f', 'c', 'x', 't')
        GROUP BY namespace.nspname, relation.relname, pgc.contype
        UNION ALL
        SELECT
            'column',
            namespace.nspname,
            relation.relname || '.' || attribute.attname
        FROM pg_attribute AS attribute
        JOIN pg_class AS relation
          ON relation.oid = attribute.attrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p')
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
        UNION ALL
        SELECT
            'column_type',
            namespace.nspname,
            relation.relname || '.' || attribute.attname || ':' ||
                format_type(attribute.atttypid, attribute.atttypmod)
        FROM pg_attribute AS attribute
        JOIN pg_class AS relation
          ON relation.oid = attribute.attrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p')
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
        UNION ALL
        SELECT
            'not_null',
            namespace.nspname,
            relation.relname || '.' || attribute.attname
        FROM pg_attribute AS attribute
        JOIN pg_class AS relation
          ON relation.oid = attribute.attrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p')
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND attribute.attnotnull
        UNION ALL
        SELECT
            'trigger',
            namespace.nspname,
            relation.relname || ':' || trg.tgname
        FROM pg_trigger AS trg
        JOIN pg_class AS relation
          ON relation.oid = trg.tgrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND NOT trg.tgisinternal
        UNION ALL
        SELECT
            'policy',
            namespace.nspname,
            relation.relname || ':' || pol.polname
        FROM pg_policy AS pol
        JOIN pg_class AS relation
          ON relation.oid = pol.polrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT
            'rule',
            namespace.nspname,
            relation.relname || ':' || rewrite.rulename
        FROM pg_rewrite AS rewrite
        JOIN pg_class AS relation
          ON relation.oid = rewrite.ev_class
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND rewrite.rulename <> '_RETURN'
        UNION ALL
        SELECT 'type', namespace.nspname, pg_type.typname
        FROM pg_type
        JOIN pg_namespace AS namespace
          ON namespace.oid = pg_type.typnamespace
        WHERE namespace.nspname = 'public'
          AND pg_type.typtype <> 'b'
          AND pg_type.typrelid = 0
        UNION ALL
        SELECT 'routine', namespace.nspname, proc.proname
        FROM pg_proc AS proc
        JOIN pg_namespace AS namespace
          ON namespace.oid = proc.pronamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT 'operator', namespace.nspname, op.oprname
        FROM pg_operator AS op
        JOIN pg_namespace AS namespace
          ON namespace.oid = op.oprnamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT 'collation', namespace.nspname, coll.collname
        FROM pg_collation AS coll
        JOIN pg_namespace AS namespace
          ON namespace.oid = coll.collnamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT 'conversion', namespace.nspname, conv.conname
        FROM pg_conversion AS conv
        JOIN pg_namespace AS namespace
          ON namespace.oid = conv.connamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT 'operator_class', namespace.nspname, opc.opcname
        FROM pg_opclass AS opc
        JOIN pg_namespace AS namespace
          ON namespace.oid = opc.opcnamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT 'operator_family', namespace.nspname, opf.opfname
        FROM pg_opfamily AS opf
        JOIN pg_namespace AS namespace
          ON namespace.oid = opf.opfnamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT 'text_search_config', namespace.nspname, config.cfgname
        FROM pg_ts_config AS config
        JOIN pg_namespace AS namespace
          ON namespace.oid = config.cfgnamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT 'text_search_dictionary', namespace.nspname, dictionary.dictname
        FROM pg_ts_dict AS dictionary
        JOIN pg_namespace AS namespace
          ON namespace.oid = dictionary.dictnamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT 'text_search_parser', namespace.nspname, parser.prsname
        FROM pg_ts_parser AS parser
        JOIN pg_namespace AS namespace
          ON namespace.oid = parser.prsnamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT 'text_search_template', namespace.nspname, template.tmplname
        FROM pg_ts_template AS template
        JOIN pg_namespace AS namespace
          ON namespace.oid = template.tmplnamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT 'extension', namespace.nspname, ext.extname
        FROM pg_extension AS ext
        JOIN pg_namespace AS namespace
          ON namespace.oid = ext.extnamespace
        WHERE namespace.nspname = 'public'
    ) AS database_objects
    ORDER BY object_schema, object_kind, object_name
"""

_MIGRATION_TABLE_PATTERN = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-z_]+)\s*\((.*?)\n\);",
    re.IGNORECASE | re.DOTALL,
)
_MIGRATION_INDEX_PATTERN = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+([a-z_]+)\s+ON\s+([a-z_]+)",
    re.IGNORECASE,
)
_IDENTITY_COLUMN_PATTERN = re.compile(
    r"^\s*([a-z_]+)\s+[^,\n]*GENERATED\s+BY\s+DEFAULT\s+AS\s+IDENTITY",
    re.IGNORECASE | re.MULTILINE,
)
_CONSTRAINT_COLUMNS_PATTERN = re.compile(
    r"\b(PRIMARY\s+KEY|UNIQUE)\s*\(([^)]*)\)",
    re.IGNORECASE,
)
_INLINE_CONSTRAINT_PATTERN = re.compile(
    r"^\s*([a-z_]+)\s+[^,\n]*\b(PRIMARY\s+KEY|UNIQUE)\b",
    re.IGNORECASE | re.MULTILINE,
)
_WORD_PATTERN = re.compile(r"[a-z_]+", re.IGNORECASE)
_COLUMN_DECLARATION_PATTERN = re.compile(
    r"^\s*([a-z_][a-z0-9_]*)\s+([a-z_][a-z0-9_]*)\b(.*)$",
    re.IGNORECASE | re.MULTILINE,
)
_CONSTRAINT_KIND_PATTERNS = {
    "p": re.compile(r"\bPRIMARY\s+KEY\b", re.IGNORECASE),
    "u": re.compile(r"\bUNIQUE\b", re.IGNORECASE),
    "f": re.compile(r"\bREFERENCES\b", re.IGNORECASE),
    "c": re.compile(r"\bCHECK\s*\(", re.IGNORECASE),
    "x": re.compile(r"\bEXCLUDE\b", re.IGNORECASE),
    "t": re.compile(r"\bCONSTRAINT\s+[a-z_][a-z0-9_]*\s+TRIGGER\b", re.IGNORECASE),
}
_TABLE_CONSTRAINT_WORDS = {
    "and",
    "check",
    "constraint",
    "delete",
    "foreign",
    "on",
    "or",
    "primary",
    "references",
    "update",
    "unique",
}
_POSTGRES_DATA_TYPES = {
    "bigint": "bigint",
    "date": "date",
    "integer": "integer",
    "text": "text",
    "timestamptz": "timestamp with time zone",
}
_SAFE_EXCEPTION_CLASS_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}")
_POSTGRES_SQLSTATE_PATTERN = re.compile(r"[0-9A-Z]{5}")


class PostgresTestSafetyError(RuntimeError):
    def __init__(self, diagnostic_code):
        self.diagnostic_code = diagnostic_code
        super().__init__(diagnostic_code)


def _connection_failure_diagnostics(error):
    exception_class = type(error).__name__
    if not _SAFE_EXCEPTION_CLASS_PATTERN.fullmatch(exception_class):
        exception_class = "UnknownException"

    try:
        diagnostic_code = getattr(error, "sqlstate", None)
    except Exception:
        diagnostic_code = None
    if (
        not isinstance(diagnostic_code, str)
        or not _POSTGRES_SQLSTATE_PATTERN.fullmatch(diagnostic_code)
    ):
        diagnostic_code = "unavailable"

    return exception_class, diagnostic_code


def _expected_postgres_test_objects(migrations_path=POSTGRES_MIGRATIONS_PATH):
    tables = {"schema_migrations"}
    sequences = set()
    indexes = {"schema_migrations_pkey"}
    constraints = {("constraint", "public", "schema_migrations:p:1")}
    expected_columns = {
        ("column", "public", "schema_migrations.version"),
        ("column", "public", "schema_migrations.applied_at"),
    }
    column_types = {
        (
            "column_type",
            "public",
            "schema_migrations.version:integer",
        ),
        (
            "column_type",
            "public",
            "schema_migrations.applied_at:timestamp with time zone",
        ),
    }
    not_null_columns = {
        ("not_null", "public", "schema_migrations.version"),
        ("not_null", "public", "schema_migrations.applied_at"),
    }

    for migration_file in sorted(
        Path(migrations_path).glob("[0-9][0-9][0-9]_*.sql")
    ):
        sql = migration_file.read_text(encoding="utf-8")
        indexes.update(
            match.group(1).lower()
            for match in _MIGRATION_INDEX_PATTERN.finditer(sql)
        )
        for match in _MIGRATION_TABLE_PATTERN.finditer(sql):
            table_name = match.group(1).lower()
            body = match.group(2)
            tables.add(table_name)
            for column, _data_type, attributes in (
                _COLUMN_DECLARATION_PATTERN.findall(body)
            ):
                if column.lower() in _TABLE_CONSTRAINT_WORDS:
                    continue
                expected_columns.add(
                    ("column", "public", f"{table_name}.{column.lower()}")
                )
                data_type = _POSTGRES_DATA_TYPES.get(_data_type.lower())
                if data_type is None:
                    raise PostgresTestSafetyError(
                        "POSTGRES_TEST_MIGRATION_SET_UNCONFIRMED"
                    )
                column_types.add(
                    (
                        "column_type",
                        "public",
                        f"{table_name}.{column.lower()}:{data_type}",
                    )
                )
                if re.search(
                    r"\bNOT\s+NULL\b|\bPRIMARY\s+KEY\b",
                    attributes,
                    re.IGNORECASE,
                ):
                    not_null_columns.add(
                        ("not_null", "public", f"{table_name}.{column.lower()}")
                    )
            for kind, pattern in _CONSTRAINT_KIND_PATTERNS.items():
                count = len(pattern.findall(body))
                if count:
                    constraints.add(
                        (
                            "constraint",
                            "public",
                            f"{table_name}:{kind}:{count}",
                        )
                    )
            sequences.update(
                f"{table_name}_{column.lower()}_seq"
                for column in _IDENTITY_COLUMN_PATTERN.findall(body)
            )
            for constraint, constraint_columns in (
                _CONSTRAINT_COLUMNS_PATTERN.findall(body)
            ):
                column_names = _WORD_PATTERN.findall(constraint_columns.lower())
                if column_names:
                    if constraint.lower() == "primary key":
                        indexes.add(f"{table_name}_pkey")
                        not_null_columns.update(
                            (
                                "not_null",
                                "public",
                                f"{table_name}.{column}",
                            )
                            for column in column_names
                        )
                    else:
                        indexes.add(
                            "_".join([table_name, *column_names, "key"])[:63]
                        )
            for column, constraint in _INLINE_CONSTRAINT_PATTERN.findall(body):
                if constraint.lower() == "primary key":
                    indexes.add(f"{table_name}_pkey")
                else:
                    indexes.add(f"{table_name}_{column.lower()}_key"[:63])

    return {
        ("table", "public", table) for table in tables
    } | {
        ("sequence", "public", sequence) for sequence in sequences
    } | {
        ("index", "public", index) for index in indexes
    } | constraints | expected_columns | column_types | not_null_columns


def _extract_catalog_objects(rows):
    objects = set()
    for row in rows:
        if isinstance(row, Mapping):
            values = tuple(row.values())
        else:
            values = tuple(row)
        if len(values) != 3:
            raise PostgresTestSafetyError(
                "POSTGRES_TEST_SCHEMA_STATE_UNCONFIRMED"
            )
        objects.add(tuple(str(value) for value in values))
    return objects


def _migration_versions(migrations_path=POSTGRES_MIGRATIONS_PATH):
    versions = []
    for migration_file in sorted(
        Path(migrations_path).glob("[0-9][0-9][0-9]_*.sql")
    ):
        version_text = migration_file.name.split("_", maxsplit=1)[0]
        if not version_text.isdigit():
            raise PostgresTestSafetyError(
                "POSTGRES_TEST_MIGRATION_SET_UNCONFIRMED"
            )
        versions.append(int(version_text))
    return versions


def _assert_empty_application_tables(connection, tables):
    for table in sorted(tables - {"schema_migrations"}):
        try:
            row = connection.execute(
                f'SELECT COUNT(*) FROM public."{table}"'
            ).fetchone()
            count = next(iter(row.values())) if isinstance(row, Mapping) else row[0]
        except Exception:
            raise PostgresTestSafetyError(
                "POSTGRES_TEST_SCHEMA_STATE_UNCONFIRMED"
            ) from None
        if count != 0:
            raise PostgresTestSafetyError("POSTGRES_TEST_DATA_NOT_EMPTY")


def _assert_prepared_schema(connection, objects, migrations_path):
    expected_objects = _expected_postgres_test_objects(migrations_path)
    if objects != expected_objects:
        raise PostgresTestSafetyError("POSTGRES_TEST_SCHEMA_NOT_EMPTY")

    try:
        rows = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        versions = [
            next(iter(row.values())) if isinstance(row, Mapping) else row[0]
            for row in rows
        ]
    except Exception:
        raise PostgresTestSafetyError(
            "POSTGRES_TEST_SCHEMA_STATE_UNCONFIRMED"
        ) from None

    expected_versions = _migration_versions(migrations_path)
    if versions != expected_versions:
        raise PostgresTestSafetyError(
            "POSTGRES_TEST_MIGRATIONS_MISMATCH"
        )
    tables = {
        name
        for object_kind, object_schema, name in expected_objects
        if object_kind == "table" and object_schema == "public"
    }
    _assert_empty_application_tables(connection, tables)


@dataclass(frozen=True)
class PostgresTestTarget:
    database: str
    host: str
    port: int
    user: str
    database_url: str = field(repr=False)


def parse_postgres_test_url(database_url):
    if (
        not isinstance(database_url, str)
        or not database_url.strip()
        or database_url != database_url.strip()
    ):
        raise PostgresTestSafetyError("POSTGRES_TEST_DATABASE_URL_REQUIRED")
    try:
        parsed = urlsplit(database_url)
        database = unquote(parsed.path.lstrip("/"))
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise PostgresTestSafetyError(
            "POSTGRES_TEST_DATABASE_URL_INVALID"
        ) from None

    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.username
        or database != TEST_DATABASE_NAME
        or host != TEST_DATABASE_HOST
        or port != TEST_DATABASE_PORT
        or parsed.query
        or parsed.fragment
    ):
        raise PostgresTestSafetyError("POSTGRES_TEST_TARGET_REJECTED")

    return PostgresTestTarget(
        database=database,
        host=host,
        port=port,
        user=unquote(parsed.username),
        database_url=database_url,
    )


def postgres_test_config(environ=None):
    settings = os.environ if environ is None else environ
    database_url = settings.get(TEST_DATABASE_URL_ENV)
    target = parse_postgres_test_url(database_url)
    return {
        "TESTING": True,
        "ENVIRONMENT": "test",
        "DATABASE_URL": target.database_url,
        "POSTGRES_TEST_MODE": True,
    }


def _require_loopback_acknowledgement(settings):
    if (
        settings.get(LOOPBACK_ACKNOWLEDGEMENT_ENV)
        != LOOPBACK_ACKNOWLEDGEMENT_VALUE
    ):
        raise PostgresTestSafetyError(
            "POSTGRES_TEST_LOOPBACK_ACKNOWLEDGEMENT_REQUIRED"
        )


def assert_postgres_test_identity(connection):
    return _assert_postgres_test_identity(connection)[0]


def _assert_postgres_test_identity(connection):
    try:
        identity = connection.execute(
            """
            SELECT current_database(), current_user,
                   inet_server_addr()::text, inet_server_port(),
                   (SELECT rolsuper FROM pg_roles
                    WHERE rolname = current_user),
                   current_setting('transaction_read_only')
            """
        ).fetchone()
        if isinstance(identity, Mapping):
            identity = tuple(identity.values())
        if identity is None or len(identity) != 6:
            raise ValueError
        database, user, host, port, is_superuser, read_only = identity
    except Exception:
        raise PostgresTestSafetyError(
            "POSTGRES_TEST_IDENTITY_UNCONFIRMED"
        ) from None

    if (
        database != TEST_DATABASE_NAME
        or host != TEST_DATABASE_HOST
        or port != TEST_DATABASE_PORT
        or not user
        or is_superuser is not False
    ):
        raise PostgresTestSafetyError("POSTGRES_TEST_TARGET_REJECTED")

    return {
        "database": database,
        "user": user,
        "server_reported_address": host,
        "port": port,
        "superuser": False,
        "read_only": read_only == "on",
        "loopback_endpoint_confirmed": True,
    }, user


def connect_postgres_test_database(
    database_url=None,
    connector=None,
    environ=None,
    row_factory=None,
):
    settings = os.environ if environ is None else environ
    _require_loopback_acknowledgement(settings)

    if database_url is None:
        database_url = settings.get(TEST_DATABASE_URL_ENV)
    target = parse_postgres_test_url(database_url)

    if connector is None:
        try:
            import psycopg
        except ImportError:
            raise PostgresTestSafetyError(
                "POSTGRES_TEST_DRIVER_UNAVAILABLE"
            ) from None
        connector = psycopg.connect

    try:
        connection_options = {
            "host": target.host,
            "hostaddr": target.host,
            "port": target.port,
            "dbname": target.database,
            "connect_timeout": 5,
        }
        if row_factory is not None:
            connection_options["row_factory"] = row_factory
        connection = connector(
            target.database_url,
            **connection_options,
        )
    except Exception as error:
        exception_class, diagnostic_code = _connection_failure_diagnostics(
            error
        )
        logger.warning(
            "PostgreSQL test connection failed: exception_class=%s "
            "diagnostic_code=%s",
            exception_class,
            diagnostic_code,
        )
        raise PostgresTestSafetyError(
            "POSTGRES_TEST_CONNECTION_FAILED"
        ) from None

    try:
        _, actual_user = _assert_postgres_test_identity(connection)
        if actual_user != target.user:
            raise PostgresTestSafetyError("POSTGRES_TEST_TARGET_REJECTED")
    except PostgresTestSafetyError:
        try:
            connection.close()
        except Exception:
            raise PostgresTestSafetyError(
                "POSTGRES_TEST_CONNECTION_CLOSE_FAILED"
            ) from None
        raise
    return connection


def prepare_postgres_test_schema(
    connection,
    environ=None,
    migrations_path=POSTGRES_MIGRATIONS_PATH,
):
    settings = os.environ if environ is None else environ
    _require_loopback_acknowledgement(settings)
    identity = assert_postgres_test_identity(connection)
    if identity["read_only"]:
        raise PostgresTestSafetyError("POSTGRES_TEST_READ_ONLY_CONNECTION")

    try:
        public_schema = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_namespace WHERE nspname = 'public'
            )
            """
        ).fetchone()
        if isinstance(public_schema, Mapping):
            public_schema = next(iter(public_schema.values()), None)
        elif public_schema is not None:
            public_schema = public_schema[0]
        if public_schema is not True:
            raise PostgresTestSafetyError(
                "POSTGRES_TEST_PUBLIC_SCHEMA_MISSING"
            )
        other_sessions = connection.execute(
            """
            SELECT COUNT(*)
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
            """
        ).fetchone()
        if isinstance(other_sessions, Mapping):
            other_sessions = next(iter(other_sessions.values()), None)
        elif other_sessions is not None:
            other_sessions = other_sessions[0]
        if other_sessions is None:
            raise PostgresTestSafetyError(
                "POSTGRES_TEST_CONNECTION_STATE_UNCONFIRMED"
            )
        if other_sessions:
            raise PostgresTestSafetyError("POSTGRES_TEST_DATABASE_IN_USE")
        existing_objects = _extract_catalog_objects(
            connection.execute(_POSTGRES_TEST_OBJECTS_QUERY).fetchall()
        )
    except PostgresTestSafetyError:
        raise
    except Exception:
        raise PostgresTestSafetyError(
            "POSTGRES_TEST_SCHEMA_STATE_UNCONFIRMED"
        ) from None

    if existing_objects:
        _assert_prepared_schema(connection, existing_objects, migrations_path)
        return {
            "status": "already_prepared",
            "applied_versions": _migration_versions(migrations_path),
        }

    try:
        result = apply_postgres_migrations(connection, migrations_path)
        other_sessions = connection.execute(
            """
            SELECT COUNT(*)
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
            """
        ).fetchone()
        if isinstance(other_sessions, Mapping):
            other_sessions = next(iter(other_sessions.values()), None)
        elif other_sessions is not None:
            other_sessions = other_sessions[0]
        if other_sessions is None:
            raise PostgresTestSafetyError(
                "POSTGRES_TEST_CONNECTION_STATE_UNCONFIRMED"
            )
        if other_sessions:
            raise PostgresTestSafetyError("POSTGRES_TEST_DATABASE_IN_USE")
        prepared_objects = _extract_catalog_objects(
            connection.execute(_POSTGRES_TEST_OBJECTS_QUERY).fetchall()
        )
        _assert_prepared_schema(connection, prepared_objects, migrations_path)
        return result
    except PostgresTestSafetyError:
        raise
    except Exception:
        raise PostgresTestSafetyError(
            "POSTGRES_TEST_SCHEMA_PREPARATION_FAILED"
        ) from None
