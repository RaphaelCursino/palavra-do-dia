import os
from datetime import timedelta


class Config:
    DATABASE_PATH = os.environ.get(
        "PALAVRA_DO_DIA_DATABASE",
        "palavra-do-dia.sqlite3",
    )
    DAILY_READING_TIMEZONE = os.environ.get(
        "PALAVRA_DO_DIA_TIMEZONE",
        "America/Sao_Paulo",
    )
    TESTING = False
    ENVIRONMENT = os.environ.get("PALAVRA_DO_DIA_ENV", "development").lower()
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=365)
