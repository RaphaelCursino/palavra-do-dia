import re
import sys
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from flask import Flask

from app import create_app
from app.db import (
    _connect_postgres_connection,
    connect_database,
    validate_neon_database_url,
)
from app.identity import csrf_token_for_visitor
from psycopg_pool import PoolTimeout


RUNTIME_URL = (
    "postgresql://runtime@ep-example-pooler.us-east-1.aws.neon.tech/"
    "palavra?sslmode=require"
)


def test_neon_runtime_url_requires_tls_and_pooler():
    endpoint, database = validate_neon_database_url(
        RUNTIME_URL,
        require_pooler=True,
    )

    assert endpoint == "ep-example.us-east-1.aws.neon.tech"
    assert database == "palavra"

    with pytest.raises(ValueError, match="pooler"):
        validate_neon_database_url(
            "postgresql://runtime@ep-example.us-east-1.aws.neon.tech/"
            "palavra?sslmode=require",
            require_pooler=True,
        )
    with pytest.raises(ValueError, match="sslmode"):
        validate_neon_database_url(
            "postgresql://runtime@ep-example-pooler.us-east-1.aws.neon.tech/"
            "palavra",
            require_pooler=True,
        )


def test_neon_migration_url_requires_direct_tls_endpoint():
    endpoint, database = validate_neon_database_url(
        "postgresql://migrator@ep-example.us-east-1.aws.neon.tech/"
        "palavra?sslmode=verify-full",
        require_pooler=False,
    )

    assert endpoint == "ep-example.us-east-1.aws.neon.tech"
    assert database == "palavra"

    with pytest.raises(ValueError, match="endpoint direto"):
        validate_neon_database_url(RUNTIME_URL, require_pooler=False)


def test_database_url_username_is_decoded_without_exposing_credentials():
    from app.commands import _database_url_username

    assert _database_url_username(
        "postgresql://palavra%5Fdia%5Fmigrator@"
        "ep-example.us-east-1.aws.neon.tech/palavra?sslmode=require"
    ) == "palavra_dia_migrator"


def test_migration_role_requires_limited_privileges_and_schema_create():
    import click

    from app.commands import _validate_migration_role

    identity = (
        "palavra_producao",
        "palavra_dia_migrator",
        False,
        False,
        False,
        True,
    )
    assert _validate_migration_role(identity, "palavra_dia_migrator") == (
        "palavra_producao",
        "palavra_dia_migrator",
    )

    for role_state in (
        ("palavra_producao", "palavra_dia_migrator", True, False, False, True),
        ("palavra_producao", "palavra_dia_migrator", False, True, False, True),
        ("palavra_producao", "palavra_dia_migrator", False, False, True, True),
        ("palavra_producao", "palavra_dia_migrator", False, False, False, False),
        ("palavra_producao", "unexpected_role", False, False, False, True),
    ):
        with pytest.raises(click.ClickException):
            _validate_migration_role(role_state, "palavra_dia_migrator")


def test_production_startup_requires_a_persistent_secret(monkeypatch, tmp_path):
    monkeypatch.delenv("PALAVRA_DO_DIA_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="PALAVRA_DO_DIA_SECRET_KEY"):
        create_app(
            {
                "ENVIRONMENT": "production",
                "SECRET_KEY": None,
                "DATABASE_URL": RUNTIME_URL,
                "DATABASE_PATH": str(tmp_path / "unused.sqlite3"),
            }
        )


def test_production_startup_requires_postgresql(tmp_path):
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app(
            {
                "ENVIRONMENT": "production",
                "SECRET_KEY": "stable-production-secret",
                "DATABASE_URL": None,
                "DATABASE_PATH": str(tmp_path / "unused.sqlite3"),
            }
        )


def test_csrf_tokens_are_stable_across_workers_and_bound_to_visitor_and_scope():
    visitor = "A" * 43
    other_visitor = "B" * 43
    first_worker_token = csrf_token_for_visitor(
        "stable-production-secret",
        visitor,
        "favorites",
    )
    second_worker_token = csrf_token_for_visitor(
        "stable-production-secret",
        visitor,
        "favorites",
    )

    assert first_worker_token == second_worker_token
    assert first_worker_token != csrf_token_for_visitor(
        "stable-production-secret",
        other_visitor,
        "favorites",
    )
    assert first_worker_token != csrf_token_for_visitor(
        "stable-production-secret",
        visitor,
        "personal",
    )


def test_csrf_token_rejects_invalid_scope_and_visitor():
    with pytest.raises(ValueError, match="Escopo"):
        csrf_token_for_visitor("secret", "A" * 43, "unknown")
    with pytest.raises(ValueError, match="Identidade"):
        csrf_token_for_visitor("secret", "invalid", "favorites")


def test_postgres_production_startup_opens_lazy_pool_without_migrating(
    monkeypatch, tmp_path
):
    import app.db as database

    pools = []

    class FakePool:
        def __init__(self, **options):
            self.options = options
            self.opened = None
            self.connection = SimpleNamespace(
                execute=lambda query, parameters=(): SimpleNamespace(
                    fetchone=lambda: (1,)
                )
            )
            self.returned = []
            pools.append(self)

        def open(self, wait):
            self.opened = wait

        def getconn(self):
            return self.connection

        def putconn(self, connection):
            self.returned.append(connection)

        def close(self):
            return None

    monkeypatch.setitem(
        sys.modules,
        "psycopg_pool",
        SimpleNamespace(ConnectionPool=FakePool),
    )
    monkeypatch.setattr(database.atexit, "register", lambda _callback: None)
    monkeypatch.setattr(
        database,
        "apply_migrations",
        lambda *_args, **_kwargs: pytest.fail(
            "Production startup must not apply migrations."
        ),
    )

    app = create_app(
        {
            "ENVIRONMENT": "production",
            "SECRET_KEY": "stable-production-secret",
            "DATABASE_URL": RUNTIME_URL,
            "DATABASE_PATH": str(tmp_path / "unused.sqlite3"),
            "POSTGRES_POOL_MAX_SIZE": 3,
        }
    )

    assert len(pools) == 1
    assert pools[0].opened is False
    assert pools[0].options["min_size"] == 0
    assert pools[0].options["max_size"] == 3
    assert pools[0].options["reconnect_timeout"] == 30
    assert pools[0].options["reconnect_failed"] is database.reconnect_failed
    assert pools[0].options["configure"] is database.configure
    assert (
        pools[0].options["connection_class"]
        is database._LoggingPostgresConnection
    )
    assert pools[0].options["name"] == "palavra-do-dia"
    assert pools[0].options["kwargs"]["prepare_threshold"] is None
    assert pools[0].options["kwargs"]["connect_timeout"] == 5
    assert app.extensions["postgres_pool"] is pools[0]
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True

    response = app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}
    assert pools[0].returned == [pools[0].connection]


def test_reconnect_failed_logs_pool_statistics_without_connection_details(
    caplog,
):
    import logging

    import app.db as database

    class FakePool:
        def get_stats(self):
            return {
                "pool_size": 1,
                "pool_max": 4,
                "pool_available": 0,
                "requests_waiting": 17,
            }

    caplog.set_level(logging.ERROR, logger="app.db")

    database.reconnect_failed(FakePool())

    assert (
        "PostgreSQL pool reconnect attempts failed "
        "(pool_size=1, pool_max=4, pool_available=0, requests_waiting=17)"
        in caplog.text
    )
    assert "postgresql://" not in caplog.text
    assert "password" not in caplog.text


def test_configure_logs_connection_success_without_querying_or_identifying(
    caplog,
):
    import logging

    import app.db as database

    class FakeConnection:
        def __str__(self):
            return "FakeConnection(user=private, password=secret)"

        def execute(self, *_args, **_kwargs):
            pytest.fail("The configure callback must not execute a query.")

    caplog.set_level(logging.INFO, logger="app.db")

    database.configure(FakeConnection())

    assert "PostgreSQL connection established" in caplog.text
    assert "private" not in caplog.text
    assert "secret" not in caplog.text
    assert "postgresql://" not in caplog.text


def test_postgres_pool_timeout_logs_capacity_without_connection_details(
    caplog,
):
    import logging

    timeout = PoolTimeout("sensitive connection details")

    class FailingPool:
        def getconn(self):
            raise timeout

        def get_stats(self):
            return {
                "pool_size": 4,
                "pool_max": 4,
                "pool_available": 0,
                "requests_waiting": 2,
            }

    app = Flask(__name__)
    app.config["DATABASE_URL"] = RUNTIME_URL
    app.extensions["postgres_pool"] = FailingPool()
    caplog.set_level(logging.ERROR, logger="app.db")

    with app.app_context(), pytest.raises(PoolTimeout) as raised:
        connect_database()

    assert raised.value is timeout
    assert (
        "pool_size=4, max_size=4, available=0, waiting=2"
        in caplog.text
    )
    assert "sensitive connection details" not in caplog.text
    assert "ep-example" not in caplog.text


def test_health_check_reports_not_ready_on_pool_timeout(
    monkeypatch, tmp_path
):
    def timeout_connect():
        raise PoolTimeout("connection unavailable")

    monkeypatch.setattr("app.db.connect_database", timeout_connect)
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": None,
            "DATABASE_PATH": str(tmp_path / "pool-timeout.sqlite3"),
        }
    )

    response = app.test_client().get("/healthz")

    assert response.status_code == 503
    assert response.json == {"status": "not_ready"}


def test_psycopg_pool_connection_warning_omits_exception_details(caplog):
    import logging

    from app.db import _configure_psycopg_pool_logging

    secret_url = (
        "postgresql://runtime:sensitive-password@"
        "ep-example-pooler.us-east-1.aws.neon.tech/palavra"
        "?password=another-secret"
    )
    pool_logger = logging.getLogger("psycopg.pool")
    _configure_psycopg_pool_logging()
    caplog.set_level(logging.WARNING, logger="psycopg.pool")

    pool_logger.warning(
        "error connecting in %r: %s",
        "palavra-do-dia",
        RuntimeError(secret_url),
    )

    assert "PostgreSQL pool diagnostic" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "sqlstate=" not in caplog.text
    assert "errno=" not in caplog.text
    assert caplog.records[-1].exc_info is None
    assert secret_url not in caplog.text
    assert "sensitive-password" not in caplog.text
    assert "another-secret" not in caplog.text


def test_psycopg_pool_logs_validated_sqlstate_and_errno(caplog):
    import logging

    from app.db import _configure_psycopg_pool_logging

    class SyntheticDatabaseError(RuntimeError):
        sqlstate = "28P01"
        errno = 111

    _configure_psycopg_pool_logging()
    caplog.set_level(logging.WARNING, logger="psycopg.pool")
    logging.getLogger("psycopg.pool").warning(
        "error connecting in %r: %s",
        "safe-pool",
        SyntheticDatabaseError(
            "postgresql://test-user:synthetic-password@"
            "db.example.test/database?options=synthetic-option"
        ),
    )

    assert "exception_type=SyntheticDatabaseError" in caplog.text
    assert "sqlstate=28P01" in caplog.text
    assert "errno=111" in caplog.text
    assert "synthetic-password" not in caplog.text
    assert "synthetic-option" not in caplog.text
    assert "postgresql://" not in caplog.text


def test_psycopg_pool_rejects_unvalidated_error_metadata(caplog):
    import logging

    from app.db import _configure_psycopg_pool_logging

    class InvalidMetadataError(RuntimeError):
        sqlstate = "28P01 contains sensitive text"
        errno = True

    _configure_psycopg_pool_logging()
    caplog.set_level(logging.WARNING, logger="psycopg.pool")
    logging.getLogger("psycopg.pool").warning(
        "error connecting in %r: %s",
        "safe-pool",
        InvalidMetadataError("synthetic-secret"),
    )

    assert "exception_type=InvalidMetadataError" in caplog.text
    assert "sqlstate=" not in caplog.text
    assert "errno=" not in caplog.text
    assert "contains sensitive text" not in caplog.text
    assert "synthetic-secret" not in caplog.text


def test_psycopg_pool_sanitizes_connection_repr(caplog):
    import logging

    from app.db import _configure_psycopg_pool_logging

    _configure_psycopg_pool_logging()
    caplog.set_level(logging.WARNING, logger="psycopg.pool")
    connection_repr = (
        "<psycopg.Connection user=test-user "
        "password=synthetic-password at 0x1234>"
    )
    logging.getLogger("psycopg.pool").warning(
        "discarding broken connection: %s",
        connection_repr,
    )

    assert "PostgreSQL pool diagnostic" in caplog.text
    assert connection_repr not in caplog.text
    assert "synthetic-password" not in caplog.text


def test_postgres_connection_failure_logs_traceback_without_credentials(
    caplog,
):
    import logging

    secret_url = (
        "postgresql://runtime:sensitive-password@"
        "ep-example-pooler.us-east-1.aws.neon.tech/palavra"
        "?password=another-secret"
    )

    caplog.set_level(logging.ERROR, logger="app.db")

    class SyntheticConnectionError(RuntimeError):
        sqlstate = "08001"
        errno = 111

    original_error = SyntheticConnectionError(secret_url)

    def failing_connect(_conninfo, **_kwargs):
        raise original_error

    with pytest.raises(SyntheticConnectionError) as error:
        _connect_postgres_connection(failing_connect, secret_url)
    assert error.value is original_error

    assert "PostgreSQL connection attempt failed" in caplog.text
    assert "exception_type=SyntheticConnectionError" in caplog.text
    assert "sqlstate=08001" in caplog.text
    assert "errno=111" in caplog.text
    assert "failing_connect" in caplog.text
    assert "sanitized traceback:" in caplog.text
    assert 'File "test_publication_setup.py"' in caplog.text
    assert secret_url not in caplog.text
    assert "sensitive-password" not in caplog.text
    assert "another-secret" not in caplog.text


def test_psycopg_pool_exception_is_sanitized_before_propagated_root_handler(
    caplog,
    monkeypatch,
):
    import logging

    from app.db import _configure_psycopg_pool_logging

    _configure_psycopg_pool_logging()
    pool_logger = logging.getLogger("psycopg.pool")
    monkeypatch.setattr(pool_logger, "propagate", True)
    caplog.set_level(logging.WARNING, logger="psycopg.pool")

    secret_password = "test-db-password-5e12"
    secret_parameter = "test-connection-option-9b73"
    secret_url = (
        "postgresql://test-user:"
        + secret_password
        + "@db.example.test/database?options="
        + secret_parameter
    )
    try:
        raise RuntimeError(secret_url)
    except RuntimeError:
        pool_logger.warning(
            "error connecting in %r: %s",
            secret_url,
            secret_url,
            exc_info=True,
        )

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert "PostgreSQL pool diagnostic" in record.getMessage()
    assert "exception_type=RuntimeError" in record.getMessage()
    assert "sqlstate=" not in record.getMessage()
    assert "errno=" not in record.getMessage()
    assert record.exc_info is None
    assert secret_url not in caplog.text
    assert secret_password not in caplog.text
    assert secret_parameter not in caplog.text
    assert "postgresql://" not in caplog.text


def test_logging_postgres_connection_routes_connect_through_safe_logger(
    monkeypatch,
):
    import app.db as database

    calls = []
    connection = object()

    def safe_connect(connect, conninfo, **kwargs):
        calls.append((connect, conninfo, kwargs))
        return connection

    monkeypatch.setattr(database, "_connect_postgres_connection", safe_connect)

    result = database._LoggingPostgresConnection.connect(
        "non-secret-conninfo",
        connect_timeout=7,
    )

    assert result is connection
    assert len(calls) == 1
    assert callable(calls[0][0])
    assert calls[0][1] == "non-secret-conninfo"
    assert calls[0][2] == {"connect_timeout": 7}


def test_health_check_reports_not_ready_without_bible_content(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": None,
            "DATABASE_PATH": str(tmp_path / "health.sqlite3"),
        }
    )

    response = app.test_client().get("/healthz")

    assert response.status_code == 503
    assert response.json == {"status": "not_ready"}


def test_health_check_reports_not_ready_without_schema(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("app.init_database", lambda _app: None)
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": None,
            "DATABASE_PATH": str(tmp_path / "not-ready.sqlite3"),
        }
    )

    response = app.test_client().get("/healthz")

    assert response.status_code == 503
    assert response.json == {"status": "not_ready"}


def test_health_check_reports_ready_with_approved_bible_content(
    app, client, daily_reading_factory
):
    daily_reading_factory("2026-06-15", translation_code="blivre")

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json == {"status": "ok"}


def test_production_csrf_token_is_visitor_bound_in_routes(tmp_path):
    app = create_app(
        {
            "TESTING": False,
            "SECRET_KEY": "stable-production-secret",
            "DATABASE_PATH": str(tmp_path / "csrf.sqlite3"),
        }
    )
    client_one = app.test_client()
    client_two = app.test_client()

    page_one = client_one.get("/progresso").get_data(as_text=True)
    token_one = re.search(
        r'name="csrf_token" value="([a-f0-9]{64})"',
        page_one,
    ).group(1)
    assert client_one.post(
        "/progresso/meta",
        data={"csrf_token": token_one, "weekly_target": "3"},
    ).status_code == 302

    page_two = client_two.get("/progresso").get_data(as_text=True)
    token_two = re.search(
        r'name="csrf_token" value="([a-f0-9]{64})"',
        page_two,
    ).group(1)
    assert token_two != token_one
    assert client_two.post(
        "/progresso/meta",
        data={"csrf_token": token_one, "weekly_target": "2"},
    ).status_code == 400


def test_verify_content_command_requires_imported_bible(sqlite_app):
    result = sqlite_app.test_cli_runner().invoke(args=["verify-content"])

    assert result.exit_code != 0
    assert "BLIVRE não foi importada" in result.output


def test_search_rejects_overlong_terms(client, daily_reading_factory):
    daily_reading_factory("2026-06-15", "demo-search")
    query = urlencode({"translation": "demo-search", "q": "x" * 121})

    response = client.get(f"/biblia/busca?{query}")

    assert response.status_code == 200
    assert "A busca deve ter no máximo 120 caracteres." in response.get_data(
        as_text=True
    )


def test_neon_command_helper_requires_interactive_terminal(
    monkeypatch, capsys
):
    from scripts import run_neon_command

    class NonInteractiveInput:
        def isatty(self):
            return False

    monkeypatch.setattr(sys, "argv", ["run_neon_command.py", "db-upgrade"])
    monkeypatch.setattr(sys, "stdin", NonInteractiveInput())

    assert run_neon_command.main() == 2
    assert "terminal interativo" in capsys.readouterr().out
