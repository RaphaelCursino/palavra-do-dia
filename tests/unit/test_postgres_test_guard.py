import pytest

import scripts.postgres_test_guard as postgres_test_guard
from scripts.postgres_test_guard import (
    PostgresTestSafetyError,
    TEST_DATABASE_HOST,
    TEST_DATABASE_NAME,
    TEST_DATABASE_PORT,
    TEST_DATABASE_URL_ENV,
    LOOPBACK_ACKNOWLEDGEMENT_ENV,
    LOOPBACK_ACKNOWLEDGEMENT_VALUE,
    assert_postgres_test_identity,
    connect_postgres_test_database,
    parse_postgres_test_url,
    postgres_test_config,
    prepare_postgres_test_schema,
    _expected_postgres_test_objects,
    _migration_versions,
)
from app.db import apply_postgres_migrations

ACKNOWLEDGED_ENVIRONMENT = {
    LOOPBACK_ACKNOWLEDGEMENT_ENV: LOOPBACK_ACKNOWLEDGEMENT_VALUE
}


def make_url(
    database=TEST_DATABASE_NAME,
    host=TEST_DATABASE_HOST,
    port=TEST_DATABASE_PORT,
):
    return f"postgresql://test_role@{host}:{port}/{database}"


class FakeResult:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = [] if rows is None else rows

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(
        self,
        database=TEST_DATABASE_NAME,
        host=TEST_DATABASE_HOST,
        port=TEST_DATABASE_PORT,
        user="test_role",
        superuser=False,
        read_only="off",
        objects=None,
        public_schema=True,
        other_sessions=0,
        fail_identity=False,
        migration_versions=None,
        table_counts=None,
    ):
        self.identity = (
            database,
            user,
            host,
            port,
            superuser,
            read_only,
        )
        self.objects = [] if objects is None else objects
        self.public_schema = public_schema
        self.other_sessions = other_sessions
        self.fail_identity = fail_identity
        self.migration_versions = (
            [] if migration_versions is None else migration_versions
        )
        self.table_counts = {} if table_counts is None else table_counts
        self.closed = False
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        if "FROM pg_stat_activity" in query:
            return FakeResult(row=(self.other_sessions,))
        if "AS database_objects" in query:
            return FakeResult(rows=self.objects)
        if "current_database()" in query:
            if self.fail_identity:
                raise RuntimeError("sensitive connection diagnostic")
            return FakeResult(row=self.identity)
        if "SELECT EXISTS" in query:
            return FakeResult(row=(self.public_schema,))
        if "SELECT version FROM schema_migrations" in query:
            return FakeResult(rows=[(version,) for version in self.migration_versions])
        if "SELECT COUNT(*) FROM public." in query:
            table = query.rsplit('"', 2)[1]
            return FakeResult(row=(self.table_counts.get(table, 0),))
        raise AssertionError("Unexpected query in mocked connection")

    def close(self):
        self.closed = True


class FakeMigrationRunnerConnection:
    def __init__(self):
        self.applied = set()
        self.events = []

    def execute(self, query, parameters=()):
        self.events.append(("execute", query, parameters))
        if query.startswith("SELECT 1"):
            return FakeResult(
                row=(1,) if parameters[0] in self.applied else None
            )
        if query.startswith("INSERT INTO schema_migrations"):
            self.applied.add(parameters[0])
        return FakeResult()

    def commit(self):
        self.events.append(("commit",))

    def rollback(self):
        self.events.append(("rollback",))


def test_accepts_only_the_expected_loopback_postgres_test_url():
    target = parse_postgres_test_url(make_url())

    assert target.database == TEST_DATABASE_NAME
    assert target.host == TEST_DATABASE_HOST
    assert target.port == TEST_DATABASE_PORT
    assert target.user == "test_role"
    assert "postgresql://" not in repr(target)


@pytest.mark.parametrize(
    "url",
    [
        make_url(database="another_database"),
        make_url(database="palavra_dia_teste"),
        make_url(host="localhost"),
        make_url(host="192.0.2.10"),
        make_url(port=5433),
        f"{make_url()}?host=remote.example",
        f"{make_url()}?options=-csearch_path=other",
    ],
)
def test_rejects_database_host_or_port_outside_the_test_target(url):
    with pytest.raises(PostgresTestSafetyError) as error:
        parse_postgres_test_url(url)

    assert error.value.diagnostic_code == "POSTGRES_TEST_TARGET_REJECTED"
    assert "postgresql://" not in str(error.value)


def test_test_config_reads_only_the_dedicated_test_database_variable():
    config = postgres_test_config(
        {TEST_DATABASE_URL_ENV: make_url(), "DATABASE_URL": "not-used"}
    )

    assert config["TESTING"] is True
    assert config["ENVIRONMENT"] == "test"
    assert config["DATABASE_URL"] == make_url()
    assert config["POSTGRES_TEST_MODE"] is True


def test_missing_dedicated_database_url_does_not_fall_back_to_database_url():
    with pytest.raises(PostgresTestSafetyError) as error:
        postgres_test_config({"DATABASE_URL": make_url()})

    assert error.value.diagnostic_code == "POSTGRES_TEST_DATABASE_URL_REQUIRED"


def test_connection_rejects_superuser_and_closes_it():
    connection = FakeConnection(superuser=True)

    with pytest.raises(PostgresTestSafetyError) as error:
        connect_postgres_test_database(
            make_url(),
            connector=lambda *_args, **_kwargs: connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_TARGET_REJECTED"
    assert connection.closed


@pytest.mark.parametrize(
    "identity",
    [
        {"database": "palavra_dia_teste"},
        {"host": "localhost"},
        {"port": 5433},
    ],
)
def test_connection_rejects_mismatched_server_identity_and_closes_it(identity):
    connection = FakeConnection(**identity)

    with pytest.raises(PostgresTestSafetyError) as error:
        connect_postgres_test_database(
            make_url(),
            connector=lambda *_args, **_kwargs: connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_TARGET_REJECTED"
    assert connection.closed


def test_connection_failure_logs_only_safe_exception_class(caplog):
    sensitive_details = (
        "postgresql://test_role:secret@remote.invalid/"
        "palavra_dia_pytest?token=hidden"
    )

    def fail_connection(*_args, **_kwargs):
        raise RuntimeError(sensitive_details)

    with pytest.raises(PostgresTestSafetyError) as error:
        connect_postgres_test_database(
            make_url(),
            connector=fail_connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_CONNECTION_FAILED"
    assert "POSTGRES_TEST_CONNECTION_FAILED" == str(error.value)
    assert "exception_class=RuntimeError" in caplog.text
    assert "diagnostic_code=unavailable" in caplog.text
    assert sensitive_details not in caplog.text
    assert "secret" not in caplog.text
    assert "token=hidden" not in caplog.text
    assert caplog.records[0].exc_info is None


def test_connection_failure_logs_valid_sqlstate_without_exception_message(
    caplog,
):
    sensitive_details = "password=secret; connection URL is private"

    class FakePsycopgError(Exception):
        sqlstate = "28P01"

    def fail_connection(*_args, **_kwargs):
        raise FakePsycopgError(sensitive_details)

    with pytest.raises(PostgresTestSafetyError) as error:
        connect_postgres_test_database(
            make_url(),
            connector=fail_connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_CONNECTION_FAILED"
    assert "exception_class=FakePsycopgError" in caplog.text
    assert "diagnostic_code=28P01" in caplog.text
    assert sensitive_details not in caplog.text
    assert "password=secret" not in caplog.text
    assert caplog.records[0].exc_info is None


def test_connection_failure_does_not_log_unvalidated_sqlstate(caplog):
    sensitive_sqlstate = "28P01; password=secret"

    class FakePsycopgError(Exception):
        sqlstate = sensitive_sqlstate

    def fail_connection(*_args, **_kwargs):
        raise FakePsycopgError("sensitive driver message")

    with pytest.raises(PostgresTestSafetyError):
        connect_postgres_test_database(
            make_url(),
            connector=fail_connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert "exception_class=FakePsycopgError" in caplog.text
    assert "diagnostic_code=unavailable" in caplog.text
    assert sensitive_sqlstate not in caplog.text
    assert "password=secret" not in caplog.text
    assert "sensitive driver message" not in caplog.text


def test_unconfirmed_identity_fails_closed_and_closes_connection():
    connection = FakeConnection(fail_identity=True)

    with pytest.raises(PostgresTestSafetyError) as error:
        connect_postgres_test_database(
            make_url(),
            connector=lambda *_args, **_kwargs: connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_IDENTITY_UNCONFIRMED"
    assert connection.closed


def test_absent_test_url_fails_before_attempting_a_connection(monkeypatch):
    monkeypatch.delenv(TEST_DATABASE_URL_ENV, raising=False)
    monkeypatch.setenv("DATABASE_URL", make_url(database="palavra_dia_teste"))
    connector_called = False

    def connector(*_args, **_kwargs):
        nonlocal connector_called
        connector_called = True

    with pytest.raises(PostgresTestSafetyError) as error:
        connect_postgres_test_database(
            connector=connector,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_DATABASE_URL_REQUIRED"
    assert connector_called is False


def test_loopback_connection_requires_acknowledging_tunnel_limitation():
    connector_called = False

    def connector(*_args, **_kwargs):
        nonlocal connector_called
        connector_called = True

    with pytest.raises(PostgresTestSafetyError) as error:
        connect_postgres_test_database(
            make_url(),
            connector=connector,
            environ={},
        )

    assert (
        error.value.diagnostic_code
        == "POSTGRES_TEST_LOOPBACK_ACKNOWLEDGEMENT_REQUIRED"
    )
    assert connector_called is False


def test_acknowledged_loopback_connects_but_does_not_claim_server_is_local():
    connection = FakeConnection()
    acknowledged_environment = {
        LOOPBACK_ACKNOWLEDGEMENT_ENV: LOOPBACK_ACKNOWLEDGEMENT_VALUE
    }
    connection_arguments = {}

    def connector(*args, **kwargs):
        connection_arguments["args"] = args
        connection_arguments["kwargs"] = kwargs
        return connection

    connected = connect_postgres_test_database(
        make_url(),
        connector=connector,
        environ=acknowledged_environment,
    )
    identity = assert_postgres_test_identity(connected)

    assert connected is connection
    assert identity["loopback_endpoint_confirmed"] is True
    assert "is_local" not in identity
    assert connection_arguments["kwargs"]["host"] == TEST_DATABASE_HOST
    assert connection_arguments["kwargs"]["hostaddr"] == TEST_DATABASE_HOST
    assert connection_arguments["kwargs"]["port"] == TEST_DATABASE_PORT
    assert connection_arguments["kwargs"]["dbname"] == TEST_DATABASE_NAME


def test_connection_rejects_server_user_different_from_url_user():
    connection = FakeConnection(user="test_role")
    url = "postgresql://different_role@127.0.0.1:5432/palavra_dia_pytest"

    with pytest.raises(PostgresTestSafetyError) as error:
        connect_postgres_test_database(
            url,
            connector=lambda *_args, **_kwargs: connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_TARGET_REJECTED"
    assert connection.closed


@pytest.mark.parametrize(
    "identity",
    [
        {"database": "palavra_dia_teste"},
        {"superuser": True},
    ],
)
def test_schema_preparation_does_not_run_migrations_for_unsafe_identity(
    monkeypatch,
    identity,
):
    connection = FakeConnection(**identity)
    migration_called = False

    def migration_runner(*_args, **_kwargs):
        nonlocal migration_called
        migration_called = True

    monkeypatch.setattr(
        postgres_test_guard,
        "apply_postgres_migrations",
        migration_runner,
    )
    with pytest.raises(PostgresTestSafetyError):
        prepare_postgres_test_schema(
            connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert migration_called is False


def test_schema_preparation_requires_loopback_limitation_acknowledgement():
    connection = FakeConnection()

    with pytest.raises(PostgresTestSafetyError) as error:
        prepare_postgres_test_schema(connection, environ={})

    assert (
        error.value.diagnostic_code
        == "POSTGRES_TEST_LOOPBACK_ACKNOWLEDGEMENT_REQUIRED"
    )
    assert connection.queries == []


def test_generic_postgres_migration_runner_is_not_test_target_coupled(tmp_path):
    migration_directory = tmp_path / "postgresql"
    migration_directory.mkdir()
    (migration_directory / "001_test.sql").write_text(
        "CREATE TABLE test_table (id BIGINT PRIMARY KEY);",
        encoding="utf-8",
    )
    connection = FakeMigrationRunnerConnection()

    apply_postgres_migrations(connection, migration_directory)

    assert connection.applied == {1}
    assert not any(
        "current_database()" in query
        for event in connection.events
        if event[0] == "execute"
        for query in (event[1],)
    )


def test_schema_preparation_requires_an_empty_schema_before_migrations(
    monkeypatch,
):
    connection = FakeConnection(
        objects=[("table", "public", "preexisting_table")]
    )
    migration_called = False

    def migration_runner(*_args, **_kwargs):
        nonlocal migration_called
        migration_called = True

    monkeypatch.setattr(
        postgres_test_guard,
        "apply_postgres_migrations",
        migration_runner,
    )
    with pytest.raises(PostgresTestSafetyError) as error:
        prepare_postgres_test_schema(
            connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_SCHEMA_NOT_EMPTY"
    assert migration_called is False


@pytest.mark.parametrize(
    "objects",
    [
        [("schema", "private", "private")],
        [("type", "public", "custom_enum")],
        [("routine", "public", "unexpected_function")],
        [("table", "public", "unexpected_table")],
    ],
)
def test_schema_preparation_rejects_other_schemas_and_public_objects(
    monkeypatch,
    objects,
):
    connection = FakeConnection(objects=objects)
    migration_called = False

    def migration_runner(*_args, **_kwargs):
        nonlocal migration_called
        migration_called = True

    monkeypatch.setattr(
        postgres_test_guard,
        "apply_postgres_migrations",
        migration_runner,
    )

    with pytest.raises(PostgresTestSafetyError) as error:
        prepare_postgres_test_schema(
            connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_SCHEMA_NOT_EMPTY"
    assert migration_called is False


def test_schema_preparation_requires_public_schema(monkeypatch):
    connection = FakeConnection(public_schema=False)
    migration_called = False

    def migration_runner(*_args, **_kwargs):
        nonlocal migration_called
        migration_called = True

    monkeypatch.setattr(
        postgres_test_guard,
        "apply_postgres_migrations",
        migration_runner,
    )

    with pytest.raises(PostgresTestSafetyError) as error:
        prepare_postgres_test_schema(
            connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_PUBLIC_SCHEMA_MISSING"
    assert migration_called is False


def test_schema_preparation_rejects_database_with_other_sessions(monkeypatch):
    connection = FakeConnection(other_sessions=1)
    migration_called = False

    def migration_runner(*_args, **_kwargs):
        nonlocal migration_called
        migration_called = True

    monkeypatch.setattr(
        postgres_test_guard,
        "apply_postgres_migrations",
        migration_runner,
    )

    with pytest.raises(PostgresTestSafetyError) as error:
        prepare_postgres_test_schema(
            connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_DATABASE_IN_USE"
    assert migration_called is False


def test_schema_preparation_runs_migrations_only_after_identity_check(
    monkeypatch,
):
    connection = FakeConnection()
    migration_calls = []

    def migration_runner(actual_connection, migrations_path):
        migration_calls.append((actual_connection, migrations_path))
        actual_connection.objects = sorted(_expected_postgres_test_objects())
        actual_connection.migration_versions = _migration_versions()

    monkeypatch.setattr(
        postgres_test_guard,
        "apply_postgres_migrations",
        migration_runner,
    )
    prepare_postgres_test_schema(
        connection,
        environ=ACKNOWLEDGED_ENVIRONMENT,
    )

    assert migration_calls == [
        (connection, postgres_test_guard.POSTGRES_MIGRATIONS_PATH)
    ]


def test_schema_preparation_accepts_only_the_exact_empty_prepared_schema(
    monkeypatch,
):
    objects = sorted(_expected_postgres_test_objects())
    connection = FakeConnection(
        objects=objects,
        migration_versions=_migration_versions(),
    )
    migration_called = False

    def migration_runner(*_args, **_kwargs):
        nonlocal migration_called
        migration_called = True

    monkeypatch.setattr(
        postgres_test_guard,
        "apply_postgres_migrations",
        migration_runner,
    )
    result = prepare_postgres_test_schema(
        connection,
        environ=ACKNOWLEDGED_ENVIRONMENT,
    )

    assert result["status"] == "already_prepared"
    assert result["applied_versions"] == _migration_versions()
    assert migration_called is False


def test_prepared_schema_rejects_persisted_application_data():
    connection = FakeConnection(
        objects=sorted(_expected_postgres_test_objects()),
        migration_versions=_migration_versions(),
        table_counts={"translations": 1},
    )

    with pytest.raises(PostgresTestSafetyError) as error:
        prepare_postgres_test_schema(
            connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_DATA_NOT_EMPTY"


@pytest.mark.parametrize(
    "extra_object",
    [
        ("schema", "unexpected", "unexpected"),
        ("index", "public", "unexpected_index"),
        ("routine", "public", "unexpected_function"),
        ("column", "public", "translations.unexpected_column"),
        ("column_type", "public", "translations.code:integer"),
        ("not_null", "public", "translations.unexpected_column"),
        ("table", "public", "Translations"),
    ],
)
def test_prepared_schema_rejects_unexpected_objects(
    extra_object,
):
    objects = sorted(_expected_postgres_test_objects() | {extra_object})
    connection = FakeConnection(
        objects=objects,
        migration_versions=_migration_versions(),
    )

    with pytest.raises(PostgresTestSafetyError) as error:
        prepare_postgres_test_schema(
            connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_SCHEMA_NOT_EMPTY"


def test_prepared_schema_rejects_missing_migration_versions():
    connection = FakeConnection(
        objects=sorted(_expected_postgres_test_objects()),
        migration_versions=[1, 2, 3, 4, 5, 6, 7],
    )

    with pytest.raises(PostgresTestSafetyError) as error:
        prepare_postgres_test_schema(
            connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_MIGRATIONS_MISMATCH"


def test_prepared_schema_rejects_missing_not_null_column():
    objects = _expected_postgres_test_objects() - {
        ("not_null", "public", "translations.code")
    }
    connection = FakeConnection(
        objects=sorted(objects),
        migration_versions=_migration_versions(),
    )

    with pytest.raises(PostgresTestSafetyError) as error:
        prepare_postgres_test_schema(
            connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_SCHEMA_NOT_EMPTY"


def test_prepared_schema_rejects_missing_column():
    objects = _expected_postgres_test_objects() - {
        ("column", "public", "translations.code")
    }
    connection = FakeConnection(
        objects=sorted(objects),
        migration_versions=_migration_versions(),
    )

    with pytest.raises(PostgresTestSafetyError) as error:
        prepare_postgres_test_schema(
            connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_SCHEMA_NOT_EMPTY"


def test_prepared_schema_rejects_column_type_mismatch():
    objects = _expected_postgres_test_objects() - {
        ("column_type", "public", "translations.code:text")
    }
    connection = FakeConnection(
        objects=sorted(objects),
        migration_versions=_migration_versions(),
    )

    with pytest.raises(PostgresTestSafetyError) as error:
        prepare_postgres_test_schema(
            connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_SCHEMA_NOT_EMPTY"


def test_schema_preparation_rejects_read_only_connection(monkeypatch):
    connection = FakeConnection(read_only="on")
    migration_called = False

    def migration_runner(*_args, **_kwargs):
        nonlocal migration_called
        migration_called = True

    monkeypatch.setattr(
        postgres_test_guard,
        "apply_postgres_migrations",
        migration_runner,
    )
    with pytest.raises(PostgresTestSafetyError) as error:
        prepare_postgres_test_schema(
            connection,
            environ=ACKNOWLEDGED_ENVIRONMENT,
        )

    assert error.value.diagnostic_code == "POSTGRES_TEST_READ_ONLY_CONNECTION"
    assert migration_called is False


def test_identity_check_reports_loopback_endpoint_without_claiming_locality():
    identity = assert_postgres_test_identity(FakeConnection())

    assert identity == {
        "database": TEST_DATABASE_NAME,
        "user": "test_role",
        "server_reported_address": TEST_DATABASE_HOST,
        "port": TEST_DATABASE_PORT,
        "superuser": False,
        "read_only": False,
        "loopback_endpoint_confirmed": True,
    }
