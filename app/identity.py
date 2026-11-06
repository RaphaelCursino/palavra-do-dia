import re
import secrets

from flask import g, session


VISITOR_SESSION_KEY = "visitor_id"
VISITOR_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


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
