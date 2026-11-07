import json
import os
from pathlib import Path
import re
import subprocess

import pytest


_GUNICORN_LOGGING_PROBE = r"""
import importlib.metadata
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import types

# Gunicorn's logging setup is portable; these POSIX APIs are only needed by
# server operations, which this isolated probe deliberately does not invoke.
for name in ("fcntl", "grp", "pwd"):
    if importlib.util.find_spec(name) is None:
        module = types.ModuleType(name)
        if name == "fcntl":
            module.F_GETFD = 1
            module.F_SETFD = 2
            module.F_GETFL = 3
            module.F_SETFL = 4
            module.FD_CLOEXEC = 1
        sys.modules[name] = module
if not hasattr(os, "geteuid"):
    os.geteuid = lambda: 0
if not hasattr(os, "getegid"):
    os.getegid = lambda: 0

from gunicorn.config import Config
from gunicorn.glogging import Logger

gunicorn_config = Config()
gunicorn_config.set("workers", int(sys.argv[2]))
gunicorn_config.set("threads", int(sys.argv[3]))
gunicorn_logger = Logger(gunicorn_config)

from app import create_app
import app.db as database
import psycopg_pool


def describe_logger(logger):
    return {
        "level": logger.level,
        "effective_level": logger.getEffectiveLevel(),
        "propagate": logger.propagate,
        "filters": [type(item).__name__ for item in logger.filters],
        "handlers": [
            {
                "type": type(handler).__name__,
                "level": handler.level,
                "stream": (
                    "stderr"
                    if getattr(handler, "stream", None) is sys.stderr
                    else "stdout"
                    if getattr(handler, "stream", None) is sys.stdout
                    else None
                ),
                "filters": [type(item).__name__ for item in handler.filters],
            }
            for handler in logger.handlers
        ],
    }


def logging_state():
    return {
        name: describe_logger(logging.getLogger(name))
        for name in ("", "app.db", "gunicorn.error")
    }


before = logging_state()
database.logger.info("RENDER_APP_DB_INFO_BEFORE_FLASK")
database.logger.warning("RENDER_APP_DB_WARNING_BEFORE_FLASK")
gunicorn_logger.info("RENDER_GUNICORN_INFO_MARKER")


class FakePool:
    def __init__(self, **_options):
        pass

    def get_stats(self):
        return {
            "pool_size": 0,
            "pool_max": 4,
            "pool_available": 0,
            "requests_waiting": 0,
        }

    def open(self, wait):
        assert wait is False

    def close(self):
        return None


psycopg_pool.ConnectionPool = FakePool
database.atexit.register = lambda _callback: None

database_path = Path(sys.argv[1])
config = {
    "TESTING": True,
    "ENVIRONMENT": "production",
    "SECRET_KEY": "synthetic-test-secret",
    "DATABASE_URL": (
        "postgresql://synthetic-user@ep-example-pooler."
        "us-east-1.aws.neon.tech/palavra?sslmode=require"
    ),
    "DATABASE_PATH": str(database_path),
}
app = create_app(config)
app_db_handler_ids_after_first_init = [
    id(handler) for handler in logging.getLogger("app.db").handlers
]
app = create_app(config)

after = logging_state()
database._connect_postgres_connection(
    lambda _conninfo, **_kwargs: object(),
    "synthetic-connection-info",
)
database.logger.info("RENDER_APP_DB_INFO_AFTER_FLASK")
database.logger.warning("RENDER_APP_DB_WARNING_AFTER_FLASK")
database.logger.error("RENDER_APP_DB_ERROR_AFTER_FLASK")

print(json.dumps({
    "gunicorn_version": importlib.metadata.version("gunicorn"),
    "loglevel": gunicorn_config.loglevel,
    "errorlog": gunicorn_config.errorlog,
    "accesslog": gunicorn_config.accesslog,
    "capture_output": gunicorn_config.capture_output,
    "workers": gunicorn_config.workers,
    "threads": gunicorn_config.threads,
    "before": before,
    "after": after,
    "pool_initialized": isinstance(app.extensions.get("postgres_pool"), FakePool),
    "app_db_handler_ids_after_first_init": app_db_handler_ids_after_first_init,
    "app_db_handler_ids_after_second_init": [
        id(handler) for handler in logging.getLogger("app.db").handlers
    ],
    "database_file_created": database_path.exists(),
}))
"""


def test_real_gunicorn_logging_and_flask_startup_are_captured_isolated(
    tmp_path,
):
    project_root = Path(__file__).resolve().parents[2]
    python = project_root / ".venv" / "Scripts" / "python.exe"
    if os.name == "nt" and not python.is_file():
        pytest.skip("O Gunicorn do venv do projeto não está disponível.")
    if os.name != "nt":
        python = Path(os.sys.executable)

    requirements = (project_root / "requirements.txt").read_text(
        encoding="utf-8"
    )
    assert re.search(r"^gunicorn>=23,<24$", requirements, re.MULTILINE)

    render_yaml = (project_root / "render.yaml").read_text(encoding="utf-8")
    start_command = re.search(
        r"^\s+startCommand:\s*(.+)$",
        render_yaml,
        flags=re.MULTILINE,
    )
    assert start_command is not None
    command = start_command.group(1)
    workers = int(re.search(r"--workers\s+(\d+)", command).group(1))
    threads = int(re.search(r"--threads\s+(\d+)", command).group(1))
    assert "--log-level" not in command
    assert "--capture-output" not in command

    database_path = tmp_path / "must-not-be-created.sqlite3"
    result = subprocess.run(
        [
            str(python),
            "-c",
            _GUNICORN_LOGGING_PROBE,
            str(database_path),
            str(workers),
            str(threads),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout.splitlines()[-1])

    assert report["gunicorn_version"] == "23.0.0"
    assert report["loglevel"] == "info"
    assert report["errorlog"] == "-"
    assert report["accesslog"] is None
    assert report["capture_output"] is False
    assert report["workers"] == workers == 2
    assert report["threads"] == threads == 4
    assert report["pool_initialized"] is True
    assert report["database_file_created"] is False

    before = report["before"]
    after = report["after"]
    assert before[""] == after[""]
    assert before[""]["effective_level"] == 30
    assert before[""]["handlers"] == []
    assert before["app.db"]["level"] == 0
    assert before["app.db"]["effective_level"] == 30
    assert before["app.db"]["propagate"] is True
    assert before["app.db"]["handlers"] == []
    assert before["gunicorn.error"]["level"] == 20
    assert before["gunicorn.error"]["effective_level"] == 20
    assert before["gunicorn.error"]["propagate"] is False
    gunicorn_error_handlers = [
        {
            "type": "StreamHandler",
            "level": 0,
            "stream": "stderr",
            "filters": [],
        }
    ]
    assert before["gunicorn.error"]["handlers"] == gunicorn_error_handlers
    assert after["app.db"]["level"] == 20
    assert after["app.db"]["effective_level"] == 20
    assert after["app.db"]["propagate"] is True
    assert after["app.db"]["handlers"] == gunicorn_error_handlers
    assert report["app_db_handler_ids_after_first_init"] == (
        report["app_db_handler_ids_after_second_init"]
    )

    assert "RENDER_GUNICORN_INFO_MARKER" in result.stderr
    assert "RENDER_APP_DB_WARNING_BEFORE_FLASK" in result.stderr
    assert "PostgreSQL physical connection attempt started" in result.stderr
    assert "PostgreSQL physical connection established" in result.stderr
    assert "RENDER_APP_DB_INFO_AFTER_FLASK" in result.stderr
    assert "RENDER_APP_DB_WARNING_AFTER_FLASK" in result.stderr
    assert "RENDER_APP_DB_ERROR_AFTER_FLASK" in result.stderr
    assert "RENDER_APP_DB_INFO_BEFORE_FLASK" not in result.stdout
    assert "RENDER_APP_DB_INFO_BEFORE_FLASK" not in result.stderr
    assert "RENDER_APP_DB_INFO_AFTER_FLASK" not in result.stdout
    assert "RENDER_GUNICORN_INFO_MARKER" not in result.stdout
    assert "synthetic-test-secret" not in result.stdout + result.stderr
    assert "ep-example-pooler" not in result.stdout + result.stderr
