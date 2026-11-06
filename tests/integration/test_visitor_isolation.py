from datetime import date

import pytest

from app import create_app
from app.db import get_db


@pytest.fixture
def isolation_clients(app, daily_reading_factory, monkeypatch):
    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    daily_reading_factory("2026-06-15", "visitor-demo")
    client_a = app.test_client()
    client_b = app.test_client()
    return client_a, client_b


def test_personal_data_isolated_between_anonymous_visitors(
    app, isolation_clients
):
    client_a, client_b = isolation_clients
    with app.app_context():
        reading_id = get_db().execute(
            """
            SELECT daily_readings.id
            FROM daily_readings
            JOIN translations ON translations.id = daily_readings.translation_id
            WHERE daily_readings.reading_date = '2026-06-15'
              AND translations.code = 'visitor-demo'
            """
        ).fetchone()["id"]
        verse_id = get_db().execute(
            """
            SELECT verses.id
            FROM verses
            JOIN translations ON translations.id = verses.translation_id
            WHERE translations.code = 'visitor-demo' AND verses.verse_number = 1
            """
        ).fetchone()["id"]

    favorite = client_a.post(
        "/favoritos/versiculo",
        data={
            "csrf_token": app.config["FAVORITES_CSRF_TOKEN"],
            "daily_reading_id": reading_id,
            "verse_number": 1,
        },
    )
    annotation = client_a.post(
        "/anotacoes/salvar",
        data={
            "csrf_token": app.config["PERSONAL_CSRF_TOKEN"],
            "verse_id": verse_id,
            "note_text": "Nota privada do visitante A.",
        },
    )
    prayer = client_a.post(
        "/oracao/salvar",
        data={
            "csrf_token": app.config["PERSONAL_CSRF_TOKEN"],
            "entry_type": "request",
            "title": "Pedido do visitante A",
            "description": "Descricao privada.",
        },
    )
    goal = client_a.post(
        "/progresso/meta",
        data={
            "csrf_token": app.config["PROGRESS_CSRF_TOKEN"],
            "weekly_target": "3",
        },
    )
    completion = client_a.post(
        "/progresso/concluir",
        data={
            "csrf_token": app.config["PROGRESS_CSRF_TOKEN"],
            "daily_reading_id": reading_id,
        },
    )
    assert all(response.status_code == 302 for response in (
        favorite, annotation, prayer, goal, completion
    ))

    assert "visitor-demo" not in client_b.get("/favoritos").get_data(as_text=True)
    assert "Nota privada do visitante A." not in client_b.get(
        "/anotacoes"
    ).get_data(as_text=True)
    assert "Pedido do visitante A" not in client_b.get("/oracao").get_data(
        as_text=True
    )
    assert 'value="3" selected' not in client_b.get(
        "/progresso"
    ).get_data(as_text=True)

    with app.app_context():
        rows = get_db().execute(
            """
            SELECT id, owner_key FROM favorites
            UNION ALL
            SELECT id, owner_key FROM verse_annotations
            UNION ALL
            SELECT id, owner_key FROM prayer_entries
            UNION ALL
            SELECT id, owner_key FROM reading_completions
            """
        ).fetchall()
        assert rows
        assert all(row["owner_key"].startswith("visitor:") for row in rows)
        favorite_id = get_db().execute(
            "SELECT id FROM favorites WHERE favorite_type = 'verse'"
        ).fetchone()["id"]
        annotation_id = get_db().execute(
            "SELECT id FROM verse_annotations"
        ).fetchone()["id"]
        prayer_id = get_db().execute(
            "SELECT id FROM prayer_entries"
        ).fetchone()["id"]
        completion_id = get_db().execute(
            "SELECT id FROM reading_completions"
        ).fetchone()["id"]

    assert client_b.post(
        f"/favoritos/{favorite_id}/remover",
        data={"csrf_token": app.config["FAVORITES_CSRF_TOKEN"]},
    ).status_code == 404
    assert client_b.post(
        f"/anotacoes/{annotation_id}/excluir",
        data={"csrf_token": app.config["PERSONAL_CSRF_TOKEN"]},
    ).status_code == 404
    assert client_b.post(
        f"/oracao/{prayer_id}/excluir",
        data={"csrf_token": app.config["PERSONAL_CSRF_TOKEN"]},
    ).status_code == 404
    assert client_b.post(
        f"/progresso/{completion_id}/desfazer",
        data={"csrf_token": app.config["PROGRESS_CSRF_TOKEN"]},
    ).status_code == 404


def test_anonymous_identity_persists_after_app_restart(tmp_path):
    database_path = str(tmp_path / "restart.sqlite3")
    config = {
        "TESTING": True,
        "DATABASE_PATH": database_path,
        "SECRET_KEY": "test-persistent-secret",
    }
    app_one = create_app(config)
    client_one = app_one.test_client()
    with client_one.session_transaction() as session:
        visitor_id = session.get("visitor_id")
    assert visitor_id is None
    client_one.get("/")
    with client_one.session_transaction() as session:
        visitor_id = session["visitor_id"]
    session_cookie = client_one.get_cookie("session")

    app_two = create_app(config)
    client_two = app_two.test_client()
    client_two.set_cookie("session", session_cookie.value)
    client_two.get("/")
    with client_two.session_transaction() as session:
        assert session["visitor_id"] == visitor_id


def test_production_requires_secret_key(monkeypatch, tmp_path):
    monkeypatch.delenv("PALAVRA_DO_DIA_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(
            {
                "ENVIRONMENT": "production",
                "DATABASE_PATH": str(tmp_path / "production.sqlite3"),
            }
        )
