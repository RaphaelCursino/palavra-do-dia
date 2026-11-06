import pytest

from app.db import get_db


def csrf(app):
    return app.config["FAVORITES_CSRF_TOKEN"]


def find_daily_reading_id(app, reading_date, translation_code="demo-test"):
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


def find_reflection_id(app, reading_date, translation_code="demo-reflection"):
    with app.app_context():
        return get_db().execute(
            """
            SELECT reflections.id
            FROM reflections
            JOIN daily_readings ON daily_readings.id = reflections.daily_reading_id
            JOIN translations ON translations.id = daily_readings.translation_id
            WHERE daily_readings.reading_date = ?
              AND translations.code = ?
            """,
            (reading_date, translation_code),
        ).fetchone()["id"]


def test_favorites_page_has_installation_scope_filters_and_empty_state(client):
    response = client.get("/favoritos")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Meus Favoritos" in page
    assert "somente neste navegador" in page
    assert "Ainda não há favoritos" in page
    assert "use “Salvar”" in page
    assert 'aria-current="page"' in page
    for filter_label in ("Todos", "Versículos", "Reflexões"):
        assert filter_label in page


def test_favorited_timestamp_uses_the_configured_local_timezone():
    from app.services.favorites import format_favorited_at

    assert format_favorited_at(
        "2026-11-06T00:30:00Z", "America/Sao_Paulo"
    ) == "quinta-feira, 5 de novembro de 2026"


def test_home_page_offers_real_favorite_actions_for_complete_reading(
    app, client, daily_reading_factory, monkeypatch
):
    from datetime import date

    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    daily_reading_factory("2026-06-15")

    page = client.get("/?translation=demo-test").get_data(as_text=True)

    assert "Salvar versículo" in page
    assert page.count('name="daily_reading_id"') == 3
    assert 'name="csrf_token"' in page
    assert "Marcar leitura como concluída" in page


def test_verse_favorite_is_persisted_with_approved_text_reference_and_translation(
    app, client, daily_reading_factory, monkeypatch
):
    from datetime import date

    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    daily_reading_factory("2026-06-15")
    reading_id = find_daily_reading_id(app, "2026-06-15")

    response = client.post(
        "/favoritos/versiculo",
        data={
            "csrf_token": csrf(app),
            "daily_reading_id": str(reading_id),
            "verse_number": "1",
            "next": "/?translation=demo-test",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].startswith(
        "/?translation=demo-test&favorite_status=verse-added"
    )
    assert "Versículo salvo nos favoritos." in client.get(
        response.headers["Location"]
    ).get_data(as_text=True)

    with app.app_context():
        favorite = get_db().execute(
            """
            SELECT favorites.owner_key, favorites.favorite_type,
                   favorites.reference_text, favorites.verse_text,
                   translations.name
            FROM favorites
            JOIN translations ON translations.id = favorites.translation_id
            """
        ).fetchone()
    assert favorite["owner_key"].startswith("visitor:")
    assert favorite["favorite_type"] == "verse"
    assert favorite["reference_text"] == "Gênesis 1:1"
    assert favorite["verse_text"] == "TEXTO SINTÉTICO demo-test — NÃO É BÍBLIA."
    assert favorite["name"] == "Demonstração de teste demo-test (NÃO É BÍBLIA)"

    saved = client.get("/favoritos?tipo=versiculos").get_data(as_text=True)
    assert "Gênesis 1:1" in saved
    assert "TEXTO SINTÉTICO demo-test — NÃO É BÍBLIA." in saved
    assert "Demonstração de teste demo-test" in saved
    assert "Salvo em" in saved
    assert "Remover" in saved


def test_verse_favorite_can_be_removed_and_is_not_duplicated(
    app, client, daily_reading_factory
):
    daily_reading_factory("2026-06-15")
    reading_id = find_daily_reading_id(app, "2026-06-15")
    post_data = {
        "csrf_token": csrf(app),
        "daily_reading_id": str(reading_id),
        "verse_number": "1",
        "next": "/favoritos?tipo=versiculos",
    }

    first = client.post("/favoritos/versiculo", data=post_data)
    second = client.post("/favoritos/versiculo", data=post_data)
    assert first.status_code == second.status_code == 302
    assert "favorite_status=verse-exists" in second.headers["Location"]
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM favorites WHERE favorite_type = 'verse'"
        ).fetchone()[0] == 1

    with app.app_context():
        favorite_id = get_db().execute("SELECT id FROM favorites").fetchone()["id"]

    removed = client.post(
        f"/favoritos/{favorite_id}/remover",
        data={"csrf_token": csrf(app), "next": "/favoritos?tipo=versiculos"},
    )
    assert removed.status_code == 302
    assert "tipo=versiculos" in removed.headers["Location"]
    assert "favorite_status=verse-removed" in removed.headers["Location"]
    assert "Ainda não há favoritos" in client.get(
        removed.headers["Location"]
    ).get_data(as_text=True)


def test_reflection_favorite_can_be_added_listed_filtered_and_removed(
    app, client, reflection_factory
):
    reflection_id = reflection_factory("2026-06-15")
    detail_before_save = client.get(
        f"/reflexoes/{reflection_id}"
    ).get_data(as_text=True)
    assert "Salvar reflexão" in detail_before_save

    response = client.post(
        "/favoritos/reflexao",
        data={
            "csrf_token": csrf(app),
            "reflection_id": str(reflection_id),
            "next": f"/reflexoes/{reflection_id}",
        },
    )

    assert response.status_code == 302
    assert f"/reflexoes/{reflection_id}?favorite_status=reflection-added" in response.headers["Location"]
    detail = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Reflexão salva nos favoritos." in detail
    assert "Remover dos favoritos" in detail

    saved = client.get("/favoritos?tipo=reflexoes").get_data(as_text=True)
    assert "Reflexão de demonstração demo-reflection (TESTE)" in saved
    assert "Gênesis 1:1–2" in saved
    assert "Leitura de segunda-feira, 15 de junho de 2026" in saved
    assert "Salvo em" in saved
    with app.app_context():
        favorite_id = get_db().execute(
            "SELECT id FROM favorites WHERE favorite_type = 'reflection'"
        ).fetchone()["id"]

    removed = client.post(
        f"/favoritos/{favorite_id}/remover",
        data={
            "csrf_token": csrf(app),
            "next": "/favoritos?tipo=reflexoes",
        },
    )
    assert removed.status_code == 302
    assert "favorite_status=reflection-removed" in removed.headers["Location"]
    assert "Ainda não há favoritos" in client.get(
        removed.headers["Location"]
    ).get_data(as_text=True)


def test_reflection_favorites_cannot_be_duplicated_or_added_from_a_draft(
    app, client, reflection_factory
):
    reflection_id = reflection_factory("2026-06-15")
    data = {"csrf_token": csrf(app), "reflection_id": reflection_id}

    assert client.post("/favoritos/reflexao", data=data).status_code == 302
    duplicate = client.post("/favoritos/reflexao", data=data)
    assert duplicate.status_code == 302
    assert "favorite_status=reflection-exists" in duplicate.headers["Location"]
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM favorites WHERE favorite_type = 'reflection'"
        ).fetchone()[0] == 1

    draft_id = reflection_factory("2026-06-16", "demo-draft", status="draft")
    assert client.post(
        "/favoritos/reflexao",
        data={"csrf_token": csrf(app), "reflection_id": draft_id},
    ).status_code == 404


def test_verse_favorites_reject_invalid_or_incomplete_readings(
    app, client, daily_reading_factory, demo_payload
):
    daily_reading_factory("2026-06-15")
    valid_id = find_daily_reading_id(app, "2026-06-15")
    common = {"csrf_token": csrf(app)}
    assert client.post(
        "/favoritos/versiculo",
        data={**common, "daily_reading_id": "9999", "verse_number": "1"},
    ).status_code == 404
    assert client.post(
        "/favoritos/versiculo",
        data={**common, "daily_reading_id": str(valid_id), "verse_number": "9"},
    ).status_code == 404

    demo_payload["books"][0]["chapters"][0]["verses"][1]["number"] = 3
    daily_reading_factory(
        "2026-06-16",
        "demo-incomplete",
        payload=demo_payload,
        start_verse=1,
        end_verse=3,
    )
    incomplete_id = find_daily_reading_id(app, "2026-06-16", "demo-incomplete")
    assert client.post(
        "/favoritos/versiculo",
        data={
            **common,
            "daily_reading_id": str(incomplete_id),
            "verse_number": "1",
        },
    ).status_code == 404


@pytest.mark.parametrize("token", ["", "invalid", "naïve"])
def test_favorite_mutations_require_csrf_token(
    app, client, daily_reading_factory, token
):
    daily_reading_factory("2026-06-15")
    reading_id = find_daily_reading_id(app, "2026-06-15")
    response = client.post(
        "/favoritos/versiculo",
        data={
            "csrf_token": token,
            "daily_reading_id": str(reading_id),
            "verse_number": "1",
        },
    )
    assert response.status_code == 400
    assert "Nenhuma alteração foi feita" in response.get_data(as_text=True)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 0


def test_favorite_return_target_cannot_redirect_to_an_external_site(
    app, client, daily_reading_factory
):
    daily_reading_factory("2026-06-15")
    reading_id = find_daily_reading_id(app, "2026-06-15")
    response = client.post(
        "/favoritos/versiculo",
        data={
            "csrf_token": csrf(app),
            "daily_reading_id": str(reading_id),
            "verse_number": "1",
            "next": "//example.invalid",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/?favorite_status=verse-added")


def test_favorites_filter_shows_only_requested_type_and_rejects_unknown_filter(
    client, daily_reading_factory, reflection_factory
):
    daily_reading_factory("2026-06-15")
    reflection_id = reflection_factory("2026-06-16")
    client.get("/")
    with client.session_transaction() as session:
        owner_key = f"visitor:{session['visitor_id']}"
    with client.application.app_context():
        connection = get_db()
        verse = connection.execute(
            """
            SELECT translations.id AS translation_id, chapters.id AS chapter_id,
                   verses.verse_number, verses.text
            FROM verses
            JOIN translations ON translations.id = verses.translation_id
            JOIN chapters ON chapters.id = verses.chapter_id
            WHERE translations.code = 'demo-test' AND verses.verse_number = 1
            """
        ).fetchone()
        connection.execute(
            """
            INSERT INTO favorites (
                owner_key, favorite_type, translation_id, chapter_id, verse_number,
                verse_text, reference_text
            ) VALUES (?, 'verse', ?, ?, ?, ?, 'Gênesis 1:1')
            """,
            (
                owner_key,
                verse["translation_id"],
                verse["chapter_id"],
                verse["verse_number"],
                verse["text"],
            ),
        )
        connection.execute(
            "INSERT INTO favorites (owner_key, favorite_type, reflection_id) VALUES (?, 'reflection', ?)",
            (owner_key, reflection_id),
        )
        connection.commit()

    verse_page = client.get("/favoritos?tipo=versiculos").get_data(as_text=True)
    reflection_page = client.get("/favoritos?tipo=reflexoes").get_data(as_text=True)
    all_page = client.get("/favoritos?tipo=todos").get_data(as_text=True)
    assert "TEXTO SINTÉTICO demo-test" in verse_page
    assert "Reflexão de demonstração" not in verse_page
    assert "Reflexão de demonstração" in reflection_page
    assert "TEXTO SINTÉTICO demo-test" not in reflection_page
    assert "TEXTO SINTÉTICO demo-test" in all_page
    assert "Reflexão de demonstração" in all_page
    assert client.get("/favoritos?tipo=desconhecido").status_code == 404
