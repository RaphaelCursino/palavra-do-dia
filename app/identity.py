import hashlib
import hmac
import re
import secrets

from flask import current_app, g, session


VISITOR_SESSION_KEY = "visitor_id"
VISITOR_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
CSRF_CONFIG_KEYS = {
    "favorites": "FAVORITES_CSRF_TOKEN",
    "progress": "PROGRESS_CSRF_TOKEN",
    "personal": "PERSONAL_CSRF_TOKEN",
}


def ensure_visitor_identity():
    visitor_id = session.get(VISITOR_SESSION_KEY)
    if not isinstance(visitor_id, str) or not VISITOR_ID_PATTERN.fullmatch(visitor_id):
        visitor_id = secrets.token_urlsafe(32)
        session[VISITOR_SESSION_KEY] = visitor_id
    session.permanent = True
    g.visitor_id = visitor_id


def current_owner_key():
    visitor_id = getattr(g, "visitor_id", None)
    if visitor_id is None:
        raise RuntimeError("A identidade anônima do visitante não foi inicializada.")
    return f"visitor:{visitor_id}"


def csrf_token_for_visitor(secret_key, visitor_id, scope):
    if scope not in CSRF_CONFIG_KEYS:
        raise ValueError("Escopo CSRF inválido.")
    if not isinstance(visitor_id, str) or not VISITOR_ID_PATTERN.fullmatch(
        visitor_id
    ):
        raise ValueError("Identidade do visitante inválida.")
    if not isinstance(secret_key, str) or not secret_key:
        raise ValueError("Chave de sessão inválida.")

    message = f"palavra-do-dia:csrf:{scope}:{visitor_id}".encode("utf-8")
    return hmac.new(
        secret_key.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def csrf_token(scope):
    config_key = CSRF_CONFIG_KEYS.get(scope)
    if config_key is None:
        raise ValueError("Escopo CSRF inválido.")

    if current_app.testing:
        test_token = current_app.config.get(config_key)
        if test_token:
            return test_token

    visitor_id = getattr(g, "visitor_id", None)
    if visitor_id is None:
        raise RuntimeError("A identidade anônima do visitante não foi inicializada.")
    return csrf_token_for_visitor(
        current_app.secret_key,
        visitor_id,
        scope,
    )


def validate_csrf_token(scope, supplied_token):
    if not isinstance(supplied_token, str) or not supplied_token.isascii():
        return False
    return hmac.compare_digest(supplied_token, csrf_token(scope))
