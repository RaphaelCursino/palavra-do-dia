import re
import sys
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from app import create_app
from app.db import validate_neon_database_url
from app.identity import csrf_token_for_visitor


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
    assert pools[0].options["kwargs"]["prepare_threshold"] is None
    assert app.extensions["postgres_pool"] is pools[0]
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True

    response = app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}
    assert pools[0].returned == [pools[0].connection]


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
