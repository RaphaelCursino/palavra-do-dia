from datetime import date

import pytest

from app.db import get_db


def csrf(app):
    return app.config["PROGRESS_CSRF_TOKEN"]


def reading_id(app, reading_date="2026-06-15", translation_code="demo-test"):
    with app.app_context():
        return get_db().execute(
            """
            SELECT daily_readings.id
            FROM daily_readings
            JOIN translations ON translations.id = daily_readings.translation_id
            WHERE daily_readings.reading_date = ?
              AND translations.code = ?
            """,
            (reading_date, translation_code),
        ).fetchone()["id"]


def test_progress_dashboard_shows_empty_calendar_and_history(client):
    response = client.get("/progresso")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Meu Progresso" in page
    assert "Ainda não há leituras concluídas" in page
    assert "Seu histórico começa com a primeira leitura" in page
    assert "Definir meta" in page
    assert "somente nesta instalação local" not in page
    assert client.get("/").status_code == 200


def test_reading_can_be_completed_once_and_undone(
    app, client, daily_reading_factory, monkeypatch
):
    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    daily_reading_factory("2026-06-15")
    identifier = reading_id(app)
    data = {"csrf_token": csrf(app), "daily_reading_id": str(identifier)}

    response = client.post("/progresso/concluir", data=data)
    assert response.status_code == 302
    assert "completion-added" in response.headers["Location"]
    assert client.post("/progresso/concluir", data=data).status_code == 302

    with app.app_context():
        connection = get_db()
        completion = connection.execute(
            "SELECT id, owner_key, completed_date FROM reading_completions"
        ).fetchone()
        assert connection.execute(
            "SELECT COUNT(*) FROM reading_completions"
        ).fetchone()[0] == 1
        assert tuple(completion) == (
            completion["id"],
            completion["owner_key"],
            "2026-06-15",
        )

    page = client.get("/progresso").get_data(as_text=True)
    assert "Dias concluídos" in page
    assert "2026-06-15: leitura concluída" in page
    assert "Gênesis 1:1–2" in page

    response = client.post(
        f"/progresso/{completion['id']}/desfazer",
        data={"csrf_token": csrf(app)},
    )
    assert response.status_code == 302
    assert "completion-removed" in response.headers["Location"]
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM reading_completions"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("reading_date", "start_verse", "end_verse"),
    [
        ("2026-06-14", 1, 2),
        ("2026-06-15", 1, 3),
    ],
)
def test_completion_rejects_non_today_or_incomplete_readings(
    app,
    client,
    daily_reading_factory,
    demo_payload,
    monkeypatch,
    reading_date,
    start_verse,
    end_verse,
):
    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    translation_code = f"demo-{reading_date}-{end_verse}"
    payload = None
    if end_verse == 3:
        import json

        payload = json.loads(json.dumps(demo_payload))
        payload["books"][0]["chapters"][0]["verses"].append(
            {"number": 3, "text": "TERCEIRO TEXTO DE TESTE — NÃO É BÍBLIA."}
        )
    daily_reading_factory(
        reading_date,
        translation_code=translation_code,
        payload=payload,
        start_verse=start_verse,
        end_verse=end_verse,
    )
    if end_verse == 3:
        with app.app_context():
            get_db().execute(
                """
                DELETE FROM verses
                WHERE verse_number = 2
                  AND translation_id = (
                      SELECT id FROM translations WHERE code = ?
                  )
                """,
                (translation_code,),
            )
            get_db().commit()
    identifier = reading_id(
        app,
        reading_date,
        translation_code=translation_code,
    )

    response = client.post(
        "/progresso/concluir",
        data={
            "csrf_token": csrf(app),
            "daily_reading_id": str(identifier),
        },
    )
    assert response.status_code == 404
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM reading_completions"
        ).fetchone()[0] == 0


def test_completion_rejects_unavailable_or_unauthorized_content(
    app, client, daily_reading_factory, monkeypatch
):
    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    daily_reading_factory("2026-06-15")
    identifier = reading_id(app)
    monkeypatch.setattr(pages, "get_reading_for_completion", lambda *_args: None)

    response = client.post(
        "/progresso/concluir",
        data={"csrf_token": csrf(app), "daily_reading_id": str(identifier)},
    )
    assert response.status_code == 404
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM reading_completions"
        ).fetchone()[0] == 0


def test_home_exposes_completion_only_for_a_valid_daily_reading(
    app, client, daily_reading_factory, monkeypatch
):
    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    empty_page = client.get("/").get_data(as_text=True)
    assert "Marcar leitura como concluída" not in empty_page

    daily_reading_factory("2026-06-15")
    page = client.get("/?translation=demo-test").get_data(as_text=True)
    assert "Marcar leitura como concluída" in page


def test_home_keeps_day_completed_when_selected_translation_changes(
    app, client, daily_reading_factory, monkeypatch
):
    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    daily_reading_factory("2026-06-15", "demo-first")
    daily_reading_factory("2026-06-15", "demo-second")
    response = client.post(
        "/progresso/concluir",
        data={
            "csrf_token": csrf(app),
            "daily_reading_id": str(reading_id(app, translation_code="demo-first")),
        },
    )
    assert response.status_code == 302

    page = client.get("/?translation=demo-second").get_data(as_text=True)
    assert "Leitura de hoje concluída." in page
    assert "Marcar leitura como concluída" not in page


def test_goal_can_be_created_updated_and_rejects_invalid_values(app, client):
    for target in ("3", "5"):
        response = client.post(
            "/progresso/meta",
            data={"csrf_token": csrf(app), "weekly_target": target},
        )
        assert response.status_code == 302

    with app.app_context():
        goal = get_db().execute(
            "SELECT owner_key, weekly_target FROM reading_goals"
        ).fetchone()
        assert tuple(goal) == (goal["owner_key"], 5)

    for invalid_target in ("0", "8", "invalid"):
        response = client.post(
            "/progresso/meta",
            data={
                "csrf_token": csrf(app),
                "weekly_target": invalid_target,
            },
        )
        assert response.status_code == 400


def test_weekly_goal_summary_counts_only_explicit_completions(
    app, client, daily_reading_factory, monkeypatch
):
    from app.routes import pages

    today = date(2026, 6, 17)
    monkeypatch.setattr(pages, "local_today", lambda _timezone: today)
    assert client.post(
        "/progresso/meta",
        data={"csrf_token": csrf(app), "weekly_target": "4"},
    ).status_code == 302

    for day in (15, 16, 17):
        monkeypatch.setattr(
            pages,
            "local_today",
            lambda _timezone, day=day: date(2026, 6, day),
        )
        reading_date = f"2026-06-{day:02d}"
        translation_code = f"weekly-{day}"
        daily_reading_factory(reading_date, translation_code)
        identifier = reading_id(
            app, reading_date, translation_code=translation_code
        )
        response = client.post(
            "/progresso/concluir",
            data={
                "csrf_token": csrf(app),
                "daily_reading_id": str(identifier),
            },
        )
        assert response.status_code == 302

    page = client.get("/progresso").get_data(as_text=True)
    assert "3 de 4 dias concluídos" in page
    assert "Falta 1 dia para alcançar sua meta." in page
    assert 'aria-valuenow="75"' in page
    assert 'aria-label="2026-06-15: leitura concluída"' in page
    assert 'aria-label="2026-06-16: leitura concluída"' in page
    assert 'aria-label="2026-06-17: leitura concluída"' in page
    assert "Neste mês: <strong>3</strong>" in page
    assert client.get("/progresso").get_data(as_text=True).count(
        "3 de 4 dias concluídos"
    ) == 1


def test_progress_mutations_require_csrf_and_unknown_ids_are_rejected(client, app):
    assert client.post(
        "/progresso/concluir",
        data={"daily_reading_id": "1"},
    ).status_code == 400
    assert client.post(
        "/progresso/concluir",
        data={"csrf_token": csrf(app), "daily_reading_id": "999999"},
    ).status_code == 404
    assert client.post(
        "/progresso/999999/desfazer",
        data={"csrf_token": csrf(app)},
    ).status_code == 404


def test_calendar_route_validates_month_and_handles_new_year(
    client, monkeypatch
):
    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 1, 2))
    december = client.get("/progresso?ano=2025&mes=12")
    page = december.get_data(as_text=True)
    assert december.status_code == 200
    assert "Dezembro 2025" in page
    assert client.get("/progresso?ano=2026").status_code == 404
    assert client.get("/progresso?ano=2026&mes=2").status_code == 404
