import importlib.util
import logging
import os
import re
from io import StringIO
from pathlib import Path

import pytest

from app import create_app
import app.db as database


_LOGGERS = (
    "",
    "app",
    "app.db",
    "psycopg.pool",
    "gunicorn",
    "gunicorn.error",
    "gunicorn.access",
)


@pytest.fixture
def clean_logging_configuration():
    loggers = {
        name: logging.getLogger(name)
        for name in _LOGGERS
    }
    original = {
        name: {
            "level": logger.level,
            "handlers": list(logger.handlers),
            "filters": list(logger.filters),
            "propagate": logger.propagate,
            "disabled": logger.disabled,
        }
        for name, logger in loggers.items()
    }

    root = loggers[""]
    root.setLevel(logging.WARNING)
    for name, logger in loggers.items():
        if name:
            logger.setLevel(logging.NOTSET)
            logger.propagate = True
        logger.handlers.clear()
        logger.filters.clear()
        logger.disabled = False

    try:
        yield loggers
    finally:
        for name, logger in loggers.items():
            saved = original[name]
            for handler in list(logger.handlers):
                if handler not in saved["handlers"]:
                    logger.removeHandler(handler)
                    handler.close()
            logger.handlers[:] = saved["handlers"]
            logger.filters[:] = saved["filters"]
            logger.setLevel(saved["level"])
            logger.propagate = saved["propagate"]
            logger.disabled = saved["disabled"]


def _capture_root(root, stream):
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.NOTSET)
    root.addHandler(handler)
    return handler


def test_app_db_inherits_python_root_warning(clean_logging_configuration):
    loggers = clean_logging_configuration
    app_db_logger = loggers["app.db"]
    stream = StringIO()
    _capture_root(loggers[""], stream)

    assert app_db_logger.level == logging.NOTSET
    assert app_db_logger.getEffectiveLevel() == logging.WARNING
    assert app_db_logger.propagate
    assert app_db_logger.handlers == []
    assert not app_db_logger.isEnabledFor(logging.INFO)

    app_db_logger.info("INFO that the default root level filters")

    assert stream.getvalue() == ""


def test_app_db_info_reaches_root_stream_handler_when_root_allows_it(
    clean_logging_configuration,
):
    loggers = clean_logging_configuration
    root = loggers[""]
    app_db_logger = loggers["app.db"]
    root.setLevel(logging.INFO)
    stream = StringIO()
    _capture_root(root, stream)

    app_db_logger.info("PostgreSQL physical connection established")

    assert app_db_logger.isEnabledFor(logging.INFO)
    assert "PostgreSQL physical connection established" in stream.getvalue()


def test_psycopg_pool_filter_does_not_discard_app_db_info(
    clean_logging_configuration,
):
    loggers = clean_logging_configuration
    root = loggers[""]
    root.setLevel(logging.INFO)
    stream = StringIO()
    _capture_root(root, stream)
    database._configure_psycopg_pool_logging()

    database.logger.info("PostgreSQL physical connection attempt started")

    assert "PostgreSQL physical connection attempt started" in stream.getvalue()


def test_psycopg_pool_filter_sanitizes_sensitive_failure(
    clean_logging_configuration,
):
    loggers = clean_logging_configuration
    stream = StringIO()
    _capture_root(loggers[""], stream)
    database._configure_psycopg_pool_logging()
    secret = "postgresql://test-user:private-password@private-host/db?token=hidden"

    try:
        raise RuntimeError(secret)
    except RuntimeError as error:
        loggers["psycopg.pool"].warning(
            "error connecting in %r: %s",
            secret,
            error,
            exc_info=True,
        )

    output = stream.getvalue()
    assert "PostgreSQL pool diagnostic" in output
    assert "exception_type=RuntimeError" in output
    assert secret not in output
    assert "private-password" not in output
    assert "private-host" not in output
    assert "token=hidden" not in output


def test_postgres_app_initialization_enables_logs_before_pool_open(
    clean_logging_configuration,
    monkeypatch,
    tmp_path,
):
    import atexit

    import psycopg_pool

    loggers = clean_logging_configuration
    root = loggers[""]
    root_level = root.level
    root_handlers = tuple(root.handlers)
    app_db_logger = loggers["app.db"]
    stream = StringIO()
    _capture_root(root, stream)
    root_handlers = tuple(root.handlers)
    opened_pools = []

    class FakePool:
        def __init__(self, **_options):
            self.opened = False
            opened_pools.append(self)

        def get_stats(self):
            return {
                "pool_size": 0,
                "pool_max": 4,
                "pool_available": 0,
                "requests_waiting": 0,
            }

        def open(self, wait):
            assert wait is False
            assert app_db_logger.getEffectiveLevel() == logging.INFO
            app_db_logger.info("INFO emitted while opening fake pool")
            self.opened = True

        def close(self):
            return None

    monkeypatch.setattr(psycopg_pool, "ConnectionPool", FakePool)
    monkeypatch.setattr(atexit, "register", lambda _callback: None)
    app_db_logger.info("INFO before PostgreSQL initialization")
    assert "INFO before PostgreSQL initialization" not in stream.getvalue()

    config = {
        "TESTING": True,
        "ENVIRONMENT": "production",
        "SECRET_KEY": "synthetic-test-secret",
        "DATABASE_URL": (
            "postgresql://synthetic-user@ep-example-pooler."
            "us-east-1.aws.neon.tech/palavra?sslmode=require"
        ),
        "DATABASE_PATH": str(tmp_path / "not-used.sqlite3"),
    }
    app = create_app(config)
    app_db_logger.info("INFO connection attempt marker")
    app_db_logger.warning("WARNING connection marker")
    app_db_logger.error("ERROR connection marker")

    assert len(opened_pools) == 1
    assert opened_pools[0].opened
    assert app.extensions["postgres_pool"] is opened_pools[0]
    assert app_db_logger.level == logging.INFO
    assert app_db_logger.getEffectiveLevel() == logging.INFO
    assert app_db_logger.propagate
    assert app_db_logger.handlers == []
    assert root.level == root_level
    assert tuple(root.handlers) == root_handlers
    assert "INFO emitted while opening fake pool" in stream.getvalue()
    assert "INFO connection attempt marker" in stream.getvalue()
    assert "WARNING connection marker" in stream.getvalue()
    assert "ERROR connection marker" in stream.getvalue()
    assert "synthetic-test-secret" not in stream.getvalue()
    assert "ep-example-pooler" not in stream.getvalue()


def test_repeated_postgres_initialization_does_not_duplicate_handlers_or_filter(
    clean_logging_configuration,
    monkeypatch,
    tmp_path,
):
    import atexit

    import psycopg_pool

    loggers = clean_logging_configuration
    root = loggers[""]
    root_handlers_before = tuple(root.handlers)

    class FakePool:
        def __init__(self, **_options):
            pass

        def get_stats(self):
            return {}

        def open(self, wait):
            assert wait is False

        def close(self):
            return None

    monkeypatch.setattr(psycopg_pool, "ConnectionPool", FakePool)
    monkeypatch.setattr(atexit, "register", lambda _callback: None)
    config = {
        "TESTING": True,
        "ENVIRONMENT": "production",
        "SECRET_KEY": "synthetic-test-secret",
        "DATABASE_URL": (
            "postgresql://synthetic-user@ep-example-pooler."
            "us-east-1.aws.neon.tech/palavra?sslmode=require"
        ),
        "DATABASE_PATH": str(tmp_path / "not-used.sqlite3"),
    }

    create_app(config)
    logger_handlers_after_first = tuple(loggers["app.db"].handlers)
    pool_filters_after_first = tuple(loggers["psycopg.pool"].filters)
    create_app(config)

    assert tuple(loggers["app.db"].handlers) == logger_handlers_after_first
    assert tuple(loggers["psycopg.pool"].filters) == pool_filters_after_first
    assert len(pool_filters_after_first) == 1
    assert tuple(root.handlers) == root_handlers_before
    assert root.level == logging.WARNING


def test_render_gunicorn_configuration_does_not_enable_app_db_info_when_installed(
    clean_logging_configuration,
):
    if importlib.util.find_spec("gunicorn") is None:
        pytest.skip("Gunicorn não está instalado neste ambiente.")
    if os.name == "nt":
        pytest.skip(
            "Gunicorn depende de módulos POSIX; o teste isolado cobre Windows."
        )

    from gunicorn.config import Config
    from gunicorn.glogging import Logger

    render_yaml = Path(__file__).resolve().parents[2] / "render.yaml"
    content = render_yaml.read_text(encoding="utf-8")
    start_command = re.search(
        r"^\s+startCommand:\s*(.+)$",
        content,
        flags=re.MULTILINE,
    )
    assert start_command is not None
    assert "gunicorn" in start_command.group(1)

    config = Config()
    if "--log-level" not in start_command.group(1):
        assert config.loglevel == "info"

    Logger(config)

    assert logging.getLogger("gunicorn.error").getEffectiveLevel() == logging.INFO
    assert logging.getLogger().getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("app.db").getEffectiveLevel() == logging.WARNING
    assert not logging.getLogger("app.db").isEnabledFor(logging.INFO)
