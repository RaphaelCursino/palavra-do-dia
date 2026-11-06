import os

import pytest

from app import create_app
from app.db import PostgresConnection, postgres_row_factory
from scripts.postgres_test_guard import (
    connect_postgres_test_database,
    postgres_test_config,
    prepare_postgres_test_schema,
)


POSTGRES_TESTS_ENABLED_ENV = "PALAVRA_DO_DIA_RUN_POSTGRES_TESTS"


class _RollbackOnlyConnection(PostgresConnection):
    SAVEPOINT = "palavra_dia_pytest_boundary"

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        if exception_type is None:
            self.commit()
        else:
            self.rollback()
        return False

    def commit(self):
        self._connection.execute(f"RELEASE SAVEPOINT {self.SAVEPOINT}")
        self._connection.execute(f"SAVEPOINT {self.SAVEPOINT}")

    def rollback(self):
        self._connection.execute(
            f"ROLLBACK TO SAVEPOINT {self.SAVEPOINT}"
        )
        self._connection.execute(f"RELEASE SAVEPOINT {self.SAVEPOINT}")
        self._connection.execute(f"SAVEPOINT {self.SAVEPOINT}")

    def close(self):
        return None


@pytest.fixture(scope="session")
def postgres_test_schema():
    if os.environ.get(POSTGRES_TESTS_ENABLED_ENV) != "1":
        pytest.skip("PostgreSQL integration tests were not explicitly enabled.")

    config = postgres_test_config()
    connection = connect_postgres_test_database(
        config["DATABASE_URL"],
        row_factory=postgres_row_factory,
    )
    try:
        return prepare_postgres_test_schema(connection)
    finally:
        connection.close()


@pytest.fixture
def app(request, monkeypatch):
    if os.environ.get(POSTGRES_TESTS_ENABLED_ENV) != "1":
        yield request.getfixturevalue("sqlite_app")
        return

    request.getfixturevalue("postgres_test_schema")
    config = postgres_test_config()
    config.update(
        {
            "PROPAGATE_EXCEPTIONS": False,
            "POSTGRES_TEST_MODE": True,
        }
    )

    import app as app_module
    import app.db as app_db

    connection = connect_postgres_test_database(
        config["DATABASE_URL"],
        row_factory=postgres_row_factory,
    )
    try:
        connection.commit()
        connection.execute("BEGIN")
        connection.execute(
            f"SAVEPOINT {_RollbackOnlyConnection.SAVEPOINT}"
        )
        guarded_connection = _RollbackOnlyConnection(connection)
        monkeypatch.setattr(app_module, "init_database", lambda _app: None)
        monkeypatch.setattr(
            app_db,
            "connect_database",
            lambda: guarded_connection,
        )
        test_app = create_app(config)
        yield test_app
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


@pytest.fixture
def postgres_transaction_connection(postgres_test_schema):
    if os.environ.get(POSTGRES_TESTS_ENABLED_ENV) != "1":
        pytest.skip("PostgreSQL integration tests were not explicitly enabled.")

    config = postgres_test_config()
    connection = connect_postgres_test_database(config["DATABASE_URL"])
    try:
        connection.commit()
        yield connection
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()
