from contextlib import closing
from datetime import date, datetime, timezone
import atexit
import logging
import re
import sqlite3
import traceback
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from flask import current_app, g
import psycopg
from psycopg_pool import PoolTimeout


MIGRATIONS_PATH = Path(__file__).resolve().parent.parent / "migrations"
POSTGRES_MIGRATIONS_PATH = MIGRATIONS_PATH / "postgresql"
REQUIRED_SCHEMA_VERSION = 8
logger = logging.getLogger(__name__)

_SAFE_EXCEPTION_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}\Z")
_SAFE_SQLSTATE = re.compile(r"[0-9A-Z]{5}\Z")
_SAFE_FRAME_NAME = re.compile(r"[A-Za-z0-9_.-]{1,120}\Z")
_CONNECTION_REPR = re.compile(
    r"<[^>]*\bConnection\b[^>]*\bat 0x[0-9a-fA-F]+>"
)


def _record_arguments(record):
    if isinstance(record.args, dict):
        return record.args.values()
    if isinstance(record.args, tuple):
        return record.args
    return (record.args,) if record.args else ()


def _pool_exception(record):
    for argument in _record_arguments(record):
        if isinstance(argument, BaseException):
            return argument
    if record.exc_info:
        return record.exc_info[1]
    return None


def _safe_exception_details(error):
    exception_name = type(error).__name__
    if not isinstance(exception_name, str) or not _SAFE_EXCEPTION_TYPE.fullmatch(
        exception_name
    ):
        exception_name = "Exception"

    details = [f"exception_type={exception_name}"]
    try:
        sqlstate = error.sqlstate
    except Exception:
        sqlstate = None
    if isinstance(sqlstate, str) and _SAFE_SQLSTATE.fullmatch(sqlstate):
        details.append(f"sqlstate={sqlstate}")

    try:
        errno = error.errno
    except Exception:
        errno = None
    if type(errno) is int and 0 <= errno <= 65535:
        details.append(f"errno={errno}")
    return ", ".join(details)


def _safe_frame_value(value):
    value = Path(value).name
    return value if _SAFE_FRAME_NAME.fullmatch(value) else "<redacted>"


class _SafePsycopgPoolLogFilter(logging.Filter):
    def filter(self, record):
        if record.name != "psycopg.pool":
            return True

        message = record.getMessage()
        arguments = _record_arguments(record)
        error = _pool_exception(record)
        has_connection = any(
            isinstance(value, psycopg.Connection)
            or (
                isinstance(value, str)
                and _CONNECTION_REPR.search(value) is not None
            )
            for value in arguments
        )
        has_connection_repr = _CONNECTION_REPR.search(message) is not None
        is_pool_failure = record.levelno >= logging.WARNING

        if error is not None or has_connection or has_connection_repr or is_pool_failure:
            details = (
                _safe_exception_details(error)
                if error is not None
                else "exception_type=unavailable"
            )
            record.msg = "PostgreSQL pool diagnostic (%s)"
            record.args = (details,)
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


_pool_log_filter = _SafePsycopgPoolLogFilter()


def _configure_psycopg_pool_logging():
    pool_logger = logging.getLogger("psycopg.pool")
    if _pool_log_filter not in pool_logger.filters:
        pool_logger.addFilter(_pool_log_filter)


def reconnect_failed(pool):
    stats = pool.get_stats()
    logger.error(
        "PostgreSQL pool reconnect attempts failed "
        "(pool_size=%d, pool_max=%d, pool_available=%d, requests_waiting=%d)",
        stats["pool_size"],
        stats["pool_max"],
        stats["pool_available"],
        stats["requests_waiting"],
    )


def configure(connection):
    logger.info("PostgreSQL connection established")


def _connect_postgres_connection(connect, conninfo, **kwargs):
    try:
        return connect(conninfo, **kwargs)
    except Exception as error:
        stack_trace = "\n".join(
            f'  File "{_safe_frame_value(frame.filename)}", '
            f"line {frame.lineno}, in {_safe_frame_value(frame.name)}"
            for frame in traceback.extract_tb(error.__traceback__)
        )
        logger.error(
            "PostgreSQL connection attempt failed "
            "(%s); sanitized traceback:\n%s",
            _safe_exception_details(error),
            stack_trace,
        )
        raise


class _LoggingPostgresConnection(psycopg.Connection):
    @classmethod
    def connect(cls, conninfo="", **kwargs):
        return _connect_postgres_connection(
            super().connect,
            conninfo,
            **kwargs,
        )


def validate_neon_database_url(database_url, require_pooler):
    try:
        parsed = urlsplit(database_url)
    except (TypeError, ValueError) as error:
        raise ValueError("A URL PostgreSQL da Neon é inválida.") from error

    host = parsed.hostname
    labels = host.lower().split(".") if host else []
    sslmodes = parse_qs(parsed.query).get("sslmode", [])
    valid_sslmode = bool(sslmodes) and sslmodes[-1] in {
        "require",
        "verify-ca",
        "verify-full",
    }
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not host
        or not host.lower().endswith(".neon.tech")
        or len(labels) < 3
        or not parsed.path.strip("/")
        or not valid_sslmode
    ):
        raise ValueError(
            "Configure uma URL PostgreSQL da Neon com sslmode=require "
            "ou validação TLS mais forte."
        )
    has_pooler = labels[0].endswith("-pooler")
    if require_pooler and not has_pooler:
        raise ValueError(
            "DATABASE_URL deve usar o endpoint pooler da Neon."
        )
    if not require_pooler and has_pooler:
        raise ValueError(
            "MIGRATION_DATABASE_URL deve usar o endpoint direto da Neon, "
            "não o pooler."
        )

    endpoint = ".".join(labels)
    if has_pooler:
        endpoint = ".".join([labels[0][:-7], *labels[1:]])
    return endpoint, unquote(parsed.path.strip("/"))


class DatabaseRow(dict):
    """Mapping row that also preserves SQLite-style positional access."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def postgres_row_factory(cursor):
    description = cursor.description
    columns = (
        [column.name for column in description]
        if description is not None
        else ()
    )

    def make_row(values):
        return DatabaseRow(
            (column, _normalize_postgres_value(value))
            for column, value in zip(columns, values)
        )

    return make_row


def _normalize_postgres_value(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
            return value.isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def get_database_backend(config=None):
    settings = current_app.config if config is None else config
    return "postgresql" if settings.get("DATABASE_URL") else "sqlite"


def _replace_sqlite_placeholders(query, adapt_nocase=False):
    result = []
    index = 0
    state = "normal"
    block_comment_depth = 0
    dollar_quote_end = None

    while index < len(query):
        character = query[index]
        next_character = query[index + 1] if index + 1 < len(query) else ""

        if state == "single":
            result.append(character)
            if character == "'" and next_character == "'":
                result.append(next_character)
                index += 2
                continue
            if character == "'":
                state = "normal"
        elif state == "double":
            result.append(character)
            if character == '"' and next_character == '"':
                result.append(next_character)
                index += 2
                continue
            if character == '"':
                state = "normal"
        elif state == "line_comment":
            result.append(character)
            if character in "\r\n":
                state = "normal"
        elif state == "block_comment":
            if character == "/" and next_character == "*":
                result.extend((character, next_character))
                block_comment_depth += 1
                index += 2
                continue
            if character == "*" and next_character == "/":
                result.extend((character, next_character))
                block_comment_depth -= 1
                index += 2
                if block_comment_depth == 0:
                    state = "normal"
                continue
            result.append(character)
        elif state == "dollar_quote":
            if query.startswith(dollar_quote_end, index):
                result.append(dollar_quote_end)
                index += len(dollar_quote_end)
                state = "normal"
                continue
            result.append(character)
        elif adapt_nocase:
            case_insensitive_like = re.match(
                r"LIKE\s+\?\s+COLLATE\s+NOCASE\b",
                query[index:],
                re.IGNORECASE,
            )
            if case_insensitive_like:
                result.append("ILIKE %s")
                index += len(case_insensitive_like.group(0))
                continue
            if character == "'":
                result.append(character)
                state = "single"
            elif character == '"':
                result.append(character)
                state = "double"
            elif character == "-" and next_character == "-":
                result.extend((character, next_character))
                state = "line_comment"
                index += 2
                continue
            elif character == "/" and next_character == "*":
                result.extend((character, next_character))
                state = "block_comment"
                block_comment_depth = 1
                index += 2
                continue
            elif character == "$":
                delimiter = re.match(
                    r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$",
                    query[index:],
                )
                if delimiter:
                    dollar_quote_end = delimiter.group(0)
                    result.append(dollar_quote_end)
                    index += len(dollar_quote_end)
                    state = "dollar_quote"
                    continue
                result.append(character)
            elif character == "?":
                result.append("%s")
            else:
                result.append(character)
        elif character == "'":
            result.append(character)
            state = "single"
        elif character == '"':
            result.append(character)
            state = "double"
        elif character == "-" and next_character == "-":
            result.extend((character, next_character))
            state = "line_comment"
            index += 2
            continue
        elif character == "/" and next_character == "*":
            result.extend((character, next_character))
            state = "block_comment"
            block_comment_depth = 1
            index += 2
            continue
        elif character == "$":
            delimiter = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", query[index:])
            if delimiter:
                dollar_quote_end = delimiter.group(0)
                result.append(dollar_quote_end)
                index += len(dollar_quote_end)
                state = "dollar_quote"
                continue
            result.append(character)
        elif character == "?":
            result.append("%s")
        else:
            result.append(character)

        index += 1

    return "".join(result)


def _adapt_postgres_query(query):
    return _replace_sqlite_placeholders(query, adapt_nocase=True)


def is_integrity_error(error):
    if isinstance(error, sqlite3.IntegrityError):
        return True
    try:
        import psycopg
    except ImportError:
        return False
    return isinstance(error, psycopg.IntegrityError)


def begin_transaction(connection):
    connection.execute(
        "BEGIN IMMEDIATE"
        if isinstance(connection, sqlite3.Connection)
        else "BEGIN"
    )


def current_timestamp_sql(connection):
    if isinstance(connection, sqlite3.Connection):
        return "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
    return "CURRENT_TIMESTAMP"


class PostgresConnection:
    def __init__(self, connection, release=None):
        self._connection = connection
        self._release = release
        self._closed = False

    def execute(self, query, parameters=()):
        return self._connection.execute(
            _adapt_postgres_query(query),
            parameters,
        )

    def executemany(self, query, parameters):
        with self._connection.cursor() as cursor:
            return cursor.executemany(
                _adapt_postgres_query(query),
                parameters,
            )

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._release is None:
            self._connection.close()
        else:
            self._release(self._connection)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exception_type, exception, traceback):
        return self._connection.__exit__(exception_type, exception, traceback)

    @property
    def in_transaction(self):
        try:
            from psycopg.pq import TransactionStatus
        except ImportError:
            return False
        return (
            self._connection.info.transaction_status
            != TransactionStatus.IDLE
        )

    def __getattr__(self, name):
        return getattr(self._connection, name)


def connect_database():
    if current_app.config.get("POSTGRES_TEST_MODE"):
        from scripts.postgres_test_guard import (
            PostgresTestSafetyError,
            connect_postgres_test_database,
        )

        database_url = current_app.config.get("DATABASE_URL")
        if not database_url:
            raise PostgresTestSafetyError(
                "POSTGRES_TEST_DATABASE_URL_REQUIRED"
            )
        connection = connect_postgres_test_database(
            database_url,
            row_factory=postgres_row_factory,
        )
        return PostgresConnection(connection)

    if get_database_backend() == "postgresql":
        pool = current_app.extensions.get("postgres_pool")
        if pool is None:
            raise RuntimeError(
                "O pool PostgreSQL não foi inicializado para este processo."
            )
        try:
            connection = pool.getconn()
        except PoolTimeout:
            stats = pool.get_stats()
            logger.error(
                "PostgreSQL connection pool timed out "
                "(pool_size=%d, max_size=%d, available=%d, waiting=%d)",
                stats["pool_size"],
                stats["pool_max"],
                stats["pool_available"],
                stats["requests_waiting"],
            )
            raise
        return PostgresConnection(connection, release=pool.putconn)

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

    if get_database_backend(app.config) == "postgresql":
        if app.config.get("POSTGRES_TEST_MODE"):
            return
        if app.config["ENVIRONMENT"] != "production":
            raise RuntimeError(
                "DATABASE_URL só pode ser usada no ambiente de produção ou "
                "no fluxo de teste PostgreSQL protegido."
            )

        pool_size = app.config["POSTGRES_POOL_MAX_SIZE"]
        pool_timeout = app.config["POSTGRES_POOL_TIMEOUT"]
        if pool_size < 1 or pool_timeout <= 0:
            raise RuntimeError("A configuração do pool PostgreSQL é inválida.")
        try:
            validate_neon_database_url(
                app.config["DATABASE_URL"],
                require_pooler=True,
            )
        except ValueError as error:
            raise RuntimeError(str(error)) from error

        try:
            from psycopg_pool import ConnectionPool
        except ImportError as error:
            raise RuntimeError(
                "A dependência psycopg[pool] é necessária para PostgreSQL."
            ) from error

        _configure_psycopg_pool_logging()
        pool = ConnectionPool(
            conninfo=app.config["DATABASE_URL"],
            connection_class=_LoggingPostgresConnection,
            name="palavra-do-dia",
            min_size=0,
            max_size=pool_size,
            timeout=pool_timeout,
            reconnect_timeout=30,
            reconnect_failed=reconnect_failed,
            configure=configure,
            max_idle=30,
            max_lifetime=300,
            kwargs={
                "row_factory": postgres_row_factory,
                "prepare_threshold": None,
                "connect_timeout": 5,
            },
            open=False,
        )
        pool.open(wait=False)
        app.extensions["postgres_pool"] = pool
        atexit.register(pool.close)
        return

    try:
        with closing(
            sqlite3.connect(app.config["DATABASE_PATH"], timeout=5)
        ) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            apply_migrations(connection, MIGRATIONS_PATH)
    except (sqlite3.Error, RuntimeError) as error:
        raise RuntimeError(
            "Não foi possível inicializar o banco SQLite."
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


def apply_postgres_migrations(
    connection,
    migrations_path=POSTGRES_MIGRATIONS_PATH,
):
    """Apply PostgreSQL migrations; callers must enforce destination policy."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.commit()

    migration_files = sorted(Path(migrations_path).glob("[0-9][0-9][0-9]_*.sql"))
    applied_count = 0
    already_applied_count = 0
    for migration_file in migration_files:
        version_text = migration_file.name.split("_", maxsplit=1)[0]
        if not version_text.isdigit():
            raise RuntimeError(f"Nome de migração inválido: {migration_file.name}")
        version = int(version_text)
        applied = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = %s",
            (version,),
        ).fetchone()
        if applied:
            already_applied_count += 1
            continue

        script = migration_file.read_text(encoding="utf-8")
        try:
            begin_transaction(connection)
            connection.execute(script)
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)",
                (version,),
            )
            connection.commit()
            applied_count += 1
        except Exception:
            connection.rollback()
            raise
    return {
        "applied_count": applied_count,
        "already_applied_count": already_applied_count,
    }
