import os
from datetime import timedelta


class Config:
    DATABASE_URL = os.environ.get("DATABASE_URL")
    MIGRATION_DATABASE_URL = os.environ.get("MIGRATION_DATABASE_URL")
    DATABASE_PATH = os.environ.get(
        "PALAVRA_DO_DIA_DATABASE",
        "palavra-do-dia.sqlite3",
    )
    POSTGRES_POOL_MAX_SIZE = int(
        os.environ.get("PALAVRA_DO_DIA_DB_POOL_MAX_SIZE", "4")
    )
    POSTGRES_POOL_TIMEOUT = float(
        os.environ.get("PALAVRA_DO_DIA_DB_POOL_TIMEOUT", "10")
    )
    DAILY_READING_TIMEZONE = os.environ.get(
        "PALAVRA_DO_DIA_TIMEZONE",
        "America/Sao_Paulo",
    )
    MAX_CONTENT_LENGTH = 16 * 1024
    TESTING = False
    ENVIRONMENT = os.environ.get("PALAVRA_DO_DIA_ENV", "development").lower()
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=365)
