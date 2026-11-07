import multiprocessing
import os
from pathlib import Path
import re
import runpy

import pytest

from app import create_app
import app.db as database


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESS_ROLE_ENV = "PALAVRA_DO_DIA_PROCESS_ROLE"


def _create_worker_app_pool(queue, database_path):
    os.environ[PROCESS_ROLE_ENV] = "master"
    config = runpy.run_path(str(PROJECT_ROOT / "gunicorn.conf.py"))
    config["post_fork"](None, None)

    app = create_app(
        {
            "TESTING": True,
            "ENVIRONMENT": "production",
            "SECRET_KEY": "synthetic-test-secret",
            "DATABASE_URL": (
                "postgresql://synthetic-user:synthetic-password@"
                "ep-example-pooler."
                "us-east-1.aws.neon.tech/palavra?sslmode=require"
            ),
            "DATABASE_PATH": str(database_path),
        }
    )
    pool = app.extensions["postgres_pool"]
    try:
        queue.put(
            {
                "pid": os.getpid(),
                "pool_id": id(pool),
                "role": database.get_process_role(),
            }
        )
    finally:
        pool.close()


def test_render_gunicorn_command_forces_no_preload():
    render_yaml = (PROJECT_ROOT / "render.yaml").read_text(
        encoding="utf-8"
    )
    start_command = re.search(
        r"^\s+startCommand:\s*(.+)$",
        render_yaml,
        flags=re.MULTILINE,
    )

    assert start_command is not None
    command = start_command.group(1).split()
    assert command[0] == "gunicorn"
    assert command[1:3] == ["--config", "gunicorn.conf.py"]
    assert "--no-preload" in command


def test_project_gunicorn_config_disables_preload_and_marks_workers(
    monkeypatch,
):
    monkeypatch.setenv(PROCESS_ROLE_ENV, "unknown")
    config = runpy.run_path(str(PROJECT_ROOT / "gunicorn.conf.py"))

    assert config["preload_app"] is False
    assert os.environ[PROCESS_ROLE_ENV] == "master"

    config["post_fork"](None, None)

    assert database.get_process_role() == "worker"


def test_postgres_pool_cannot_initialize_in_gunicorn_master(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(PROCESS_ROLE_ENV, "master")

    with pytest.raises(
        RuntimeError,
        match="O pool PostgreSQL só pode ser inicializado em um worker",
    ):
        create_app(
            {
                "TESTING": True,
                "ENVIRONMENT": "production",
                "SECRET_KEY": "synthetic-test-secret",
                "DATABASE_URL": (
                    "postgresql://synthetic-user:synthetic-password@"
                    "ep-example-pooler."
                    "us-east-1.aws.neon.tech/palavra?sslmode=require"
                ),
                "DATABASE_PATH": str(tmp_path / "not-used.sqlite3"),
            }
        )


def test_postgres_pool_initialization_is_idempotent_per_flask_app(
    monkeypatch,
    tmp_path,
    caplog,
):
    import atexit
    import logging

    registered_callbacks = []
    monkeypatch.setattr(atexit, "register", registered_callbacks.append)
    monkeypatch.setenv(PROCESS_ROLE_ENV, "worker")
    caplog.set_level(logging.INFO, logger="app.db")
    app = create_app(
        {
            "TESTING": True,
            "ENVIRONMENT": "production",
            "SECRET_KEY": "synthetic-test-secret",
            "DATABASE_URL": (
                "postgresql://synthetic-user:synthetic-password@"
                "ep-example-pooler."
                "us-east-1.aws.neon.tech/palavra?sslmode=require"
            ),
            "DATABASE_PATH": str(tmp_path / "not-used.sqlite3"),
        }
    )
    pool = app.extensions["postgres_pool"]

    try:
        database.init_app(app)

        assert app.extensions["postgres_pool"] is pool
        assert registered_callbacks == [pool.close]
        assert f"pid={os.getpid()}" in caplog.text
        assert "process_role=worker" in caplog.text
        assert "synthetic-test-secret" not in caplog.text
        assert "synthetic-password" not in caplog.text
        assert "ep-example-pooler" not in caplog.text
    finally:
        pool.close()


def test_each_worker_process_creates_its_own_real_pool(tmp_path):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_create_worker_app_pool,
            args=(queue, str(tmp_path / f"worker-{index}.sqlite3")),
        )
        for index in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)

    try:
        assert all(process.exitcode == 0 for process in processes)
        results = [queue.get(timeout=5) for _ in processes]
        assert len({result["pid"] for result in results}) == 2
        assert all(result["role"] == "worker" for result in results)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        queue.close()
        queue.join_thread()
