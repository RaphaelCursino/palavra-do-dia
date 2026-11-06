from pathlib import Path
import os
import secrets
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask
import psycopg

from .commands import register_commands
from .config import Config
from .db import (
    REQUIRED_SCHEMA_VERSION,
    get_db,
    init_app as init_database,
)
from .errors import register_error_handlers
from .routes.pages import pages
from .services.daily_reading import format_date_pt, local_today
from .repositories.personal import get_reader_preferences
from .identity import (
    csrf_token,
    ensure_visitor_identity,
    current_owner_key,
)


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    if test_config is not None:
        app.config.update(test_config)

    environment = app.config["ENVIRONMENT"].lower()
    secret_key = app.config.get("SECRET_KEY") or secrets.token_urlsafe(48)
    if environment == "production" and not app.config.get("SECRET_KEY"):
        configured_secret = os.environ.get("PALAVRA_DO_DIA_SECRET_KEY")
        if not configured_secret:
            raise RuntimeError(
                "PALAVRA_DO_DIA_SECRET_KEY é obrigatória em produção."
            )
        secret_key = configured_secret
    app.config["SECRET_KEY"] = secret_key
    if environment == "production" and not app.config.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL é obrigatória em produção.")
    app.config["SESSION_COOKIE_SECURE"] = environment == "production"

    if app.config["TESTING"]:
        app.config.setdefault("FAVORITES_CSRF_TOKEN", secrets.token_urlsafe(32))
        app.config.setdefault("PROGRESS_CSRF_TOKEN", secrets.token_urlsafe(32))
        app.config.setdefault("PERSONAL_CSRF_TOKEN", secrets.token_urlsafe(32))

    try:
        ZoneInfo(app.config["DAILY_READING_TIMEZONE"])
    except (ZoneInfoNotFoundError, TypeError) as error:
        raise RuntimeError(
            "PALAVRA_DO_DIA_TIMEZONE deve ser um fuso horário IANA válido."
        ) from error

    database_path = Path(app.config["DATABASE_PATH"])
    if not database_path.is_absolute():
        database_path = Path(app.instance_path) / database_path
    app.config["DATABASE_PATH"] = str(database_path.resolve())

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    init_database(app)
    app.register_blueprint(pages)
    register_error_handlers(app)
    register_commands(app)
    app.before_request(ensure_visitor_identity)

    @app.get("/healthz")
    def health_check():
        try:
            ready = get_db().execute(
                """
                SELECT 1
                FROM schema_migrations
                WHERE version = ?
                  AND EXISTS (
                      SELECT 1
                      FROM translations
                      WHERE code = 'blivre'
                        AND license_status = 'approved'
                        AND EXISTS (
                            SELECT 1
                            FROM verses
                            WHERE verses.translation_id = translations.id
                        )
                  )
                """,
                (REQUIRED_SCHEMA_VERSION,),
            ).fetchone()
        except (sqlite3.Error, psycopg.Error):
            return {"status": "not_ready"}, 503
        if ready is None:
            return {"status": "not_ready"}, 503
        return {"status": "ok"}, 200

    @app.context_processor
    def inject_current_date():
        today = local_today(app.config["DAILY_READING_TIMEZONE"])
        return {
            "today_label": format_date_pt(today),
            "today_iso": today.isoformat(),
            "favorites_csrf_token": csrf_token("favorites"),
            "progress_csrf_token": csrf_token("progress"),
            "reader_preferences": get_reader_preferences(
                get_db(), current_owner_key()
            ),
            "personal_csrf_token": csrf_token("personal"),
        }

    return app
