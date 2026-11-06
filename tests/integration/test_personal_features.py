import pytest

from app.db import get_db


def personal_csrf(app):
    return app.config["PERSONAL_CSRF_TOKEN"]


def verse_id_for(app, translation_code):
    with app.app_context():
        return get_db().execute(
            """
            SELECT verses.id
            FROM verses
            JOIN translations ON translations.id = verses.translation_id
            WHERE translations.code = ? AND verses.verse_number = 1
            """,
            (translation_code,),
        ).fetchone()["id"]


def test_annotations_are_private_editable_and_link_to_the_original_verse(
    app, client, daily_reading_factory
):
    daily_reading_factory("2026-06-15", "notes-demo")
    identifier = verse_id_for(app, "notes-demo")
    response = client.post(
        "/anotacoes/salvar",
        data={
            "csrf_token": personal_csrf(app),
            "verse_id": str(identifier),
            "note_text": "  Minha observação pessoal.  ",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("#verse-1")

    with app.app_context():
        note = get_db().execute(
            "SELECT owner_key, verse_id, note_text FROM verse_annotations"
        ).fetchone()
        assert tuple(note) == (
            note["owner_key"],
            identifier,
            "Minha observação pessoal.",
        )

    page = client.get("/anotacoes")
    assert "Minhas Anotações" in page.get_data(as_text=True)
    assert 'href="/biblia/notes-demo/GEN/1#verse-1"' in page.get_data(as_text=True)

    edited = client.post(
        "/anotacoes/salvar",
        data={
            "csrf_token": personal_csrf(app),
            "verse_id": str(identifier),
            "note_text": "Anotação revisada.",
        },
    )
    assert edited.status_code == 302
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM verse_annotations"
        ).fetchone()[0] == 1
        assert get_db().execute(
            "SELECT note_text FROM verse_annotations"
        ).fetchone()["note_text"] == "Anotação revisada."

    chapter = client.get("/biblia/notes-demo/GEN/1").get_data(as_text=True)
    assert "Anotação revisada." in chapter
    assert "TEXTO SINTÉTICO notes-demo — NÃO É BÍBLIA." in chapter

    note_row = None
    with app.app_context():
        note_row = get_db().execute(
            "SELECT id FROM verse_annotations"
        ).fetchone()["id"]
    removed = client.post(
        f"/anotacoes/{note_row}/excluir",
        data={"csrf_token": personal_csrf(app)},
    )
    assert removed.status_code == 302
    assert client.get("/anotacoes").get_data(as_text=True).find(
        "Suas anotações começam na leitura"
    ) >= 0


def test_annotation_validates_csrf_content_and_approved_verse(
    app, client, daily_reading_factory
):
    daily_reading_factory("2026-06-15", "annotation-security")
    identifier = verse_id_for(app, "annotation-security")
    assert client.post(
        "/anotacoes/salvar",
        data={"verse_id": identifier, "note_text": "teste"},
    ).status_code == 400
    assert client.post(
        "/anotacoes/salvar",
        data={
            "csrf_token": personal_csrf(app),
            "verse_id": identifier,
            "note_text": "   ",
        },
    ).status_code == 400
    assert client.post(
        "/anotacoes/salvar",
        data={
            "csrf_token": personal_csrf(app),
            "verse_id": "999999",
            "note_text": "Anotação",
        },
    ).status_code == 404


def test_highlighting_uses_existing_favorite_and_supports_category_color_filters(
    app, client, daily_reading_factory
):
    daily_reading_factory("2026-06-15", "mark-demo")
    data = {
        "csrf_token": personal_csrf(app),
        "translation_code": "mark-demo",
        "book_code": "GEN",
        "chapter_number": "1",
        "verse_number": "1",
        "color": "blue",
        "category": "hope",
    }
    response = client.post("/favoritos/marcacao", data=data)
    assert response.status_code == 302
    with app.app_context():
        connection = get_db()
        assert connection.execute(
            "SELECT COUNT(*) FROM favorites WHERE favorite_type = 'verse'"
        ).fetchone()[0] == 1
        assert tuple(
            connection.execute(
                "SELECT color, category FROM verse_highlights"
            ).fetchone()
        ) == ("blue", "hope")

    chapter = client.get("/biblia/mark-demo/GEN/1").get_data(as_text=True)
    assert "highlight-blue" in chapter
    favorites = client.get("/favoritos?tipo=versiculos&categoria=hope&cor=blue")
    assert "Marcado em Azul" in favorites.get_data(as_text=True)
    assert client.get(
        "/favoritos?tipo=versiculos&categoria=faith&cor=rose"
    ).get_data(as_text=True).find("Marcado em Azul") == -1

    favorite_id = None
    with app.app_context():
        favorite_id = get_db().execute(
            "SELECT id FROM favorites WHERE favorite_type = 'verse'"
        ).fetchone()["id"]
    updated = client.post(
        f"/favoritos/{favorite_id}/marcacao",
        data={
            "csrf_token": personal_csrf(app),
            "color": "gold",
            "category": "gratitude",
        },
    )
    assert updated.status_code == 302
    removed = client.post(
        f"/favoritos/{favorite_id}/marcacao/remover",
        data={"csrf_token": personal_csrf(app)},
    )
    assert removed.status_code == 302
    with app.app_context():
        connection = get_db()
        assert connection.execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM verse_highlights"
        ).fetchone()[0] == 0


def test_highlights_reject_invalid_values_and_csrf(app, client, daily_reading_factory):
    daily_reading_factory("2026-06-15", "mark-invalid")
    data = {
        "translation_code": "mark-invalid",
        "book_code": "GEN",
        "chapter_number": "1",
        "verse_number": "1",
        "color": "ultraviolet",
        "category": "hope",
    }
    assert client.post("/favoritos/marcacao", data=data).status_code == 400
    data.update(csrf_token=personal_csrf(app), color="blue", category="unknown")
    assert client.post("/favoritos/marcacao", data=data).status_code == 400


def test_prayer_journal_create_edit_answer_filter_and_delete(app, client):
    response = client.post(
        "/oracao/salvar",
        data={
            "csrf_token": personal_csrf(app),
            "entry_type": "request",
            "title": "Por serenidade",
            "description": "Um registro privado.",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        entry = get_db().execute(
            "SELECT id, owner_key, status FROM prayer_entries"
        ).fetchone()
        assert tuple(entry) == (entry["id"], entry["owner_key"], "active")
        entry_id = entry["id"]

    edit_page = client.get(f"/oracao?editar={entry_id}")
    assert "Por serenidade" in edit_page.get_data(as_text=True)
    updated = client.post(
        "/oracao/salvar",
        data={
            "csrf_token": personal_csrf(app),
            "entry_id": str(entry_id),
            "entry_type": "request",
            "title": "Serenidade e confiança",
            "description": "Descrição atualizada.",
        },
    )
    assert updated.status_code == 302
    assert "Descrição atualizada." in client.get("/oracao").get_data(as_text=True)

    answered = client.post(
        f"/oracao/{entry_id}/respondida",
        data={"csrf_token": personal_csrf(app)},
    )
    assert answered.status_code == 302
    with app.app_context():
        entry = get_db().execute(
            "SELECT status, answered_at FROM prayer_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        assert entry["status"] == "answered"
        assert entry["answered_at"]
    assert "Serenidade e confiança" in client.get(
        "/oracao?status=answered&tipo=request"
    ).get_data(as_text=True)
    assert "Serenidade e confiança" not in client.get(
        "/oracao?status=active"
    ).get_data(as_text=True)

    assert client.post(
        f"/oracao/{entry_id}/excluir",
        data={"csrf_token": personal_csrf(app)},
    ).status_code == 302
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM prayer_entries"
        ).fetchone()[0] == 0


def test_prayer_journal_gratitude_and_validation(app, client):
    assert client.post(
        "/oracao/salvar",
        data={
            "csrf_token": personal_csrf(app),
            "entry_type": "gratitude",
            "title": "Gratidão",
            "description": "Pelo dia de hoje.",
        },
    ).status_code == 302
    with app.app_context():
        entry_id = get_db().execute(
            "SELECT id FROM prayer_entries"
        ).fetchone()["id"]
    assert client.post(
        f"/oracao/{entry_id}/respondida",
        data={"csrf_token": personal_csrf(app)},
    ).status_code == 404
    assert client.post(
        "/oracao/salvar",
        data={
            "csrf_token": personal_csrf(app),
            "entry_type": "unknown",
            "title": "",
            "description": "x",
        },
    ).status_code == 400
    assert client.post(
        "/oracao/salvar",
        data={"entry_type": "request", "title": "x", "description": "y"},
    ).status_code == 400


def test_reader_preferences_are_persisted_applied_and_validated(app, client):
    response = client.post(
        "/configuracoes",
        data={
            "csrf_token": personal_csrf(app),
            "theme": "dark",
            "font_scale": "130",
            "verse_spacing": "compact",
        },
    )
    assert response.status_code == 302
    settings = client.get("/configuracoes")
    assert 'data-theme="dark"' in settings.get_data(as_text=True)
    assert 'value="130" data-range-output' in settings.get_data(as_text=True)
    bible = client.get("/biblia").get_data(as_text=True)
    assert 'data-theme="dark" data-font-scale="130" data-verse-spacing="compact"' in bible

    for data in (
        {"theme": "automatic", "font_scale": "100", "verse_spacing": "comfortable"},
        {"theme": "light", "font_scale": "125", "verse_spacing": "comfortable"},
        {"theme": "light", "font_scale": "100", "verse_spacing": "loose"},
    ):
        data["csrf_token"] = personal_csrf(app)
        assert client.post("/configuracoes", data=data).status_code == 400
    assert client.post(
        "/configuracoes",
        data={"theme": "light", "font_scale": "100", "verse_spacing": "compact"},
    ).status_code == 400


def test_personal_pages_show_empty_states_and_filter_validation(client):
    assert "Suas anotações começam na leitura" in client.get(
        "/anotacoes"
    ).get_data(as_text=True)
    assert "Nenhum registro neste filtro" in client.get(
        "/oracao"
    ).get_data(as_text=True)
    assert client.get("/oracao?status=unlisted").status_code == 404
    assert client.get("/favoritos?categoria=unlisted").status_code == 404


@pytest.mark.parametrize(
    ("path", "label"),
    [
        ("/", "Palavra do Dia"),
        ("/biblia", "Bíblia Sagrada"),
        ("/reflexoes", "Reflexões"),
        ("/favoritos", "Meus"),
        ("/anotacoes", "Minhas Anotações"),
        ("/oracao", "Diário de"),
        ("/progresso", "Meu Progresso"),
        ("/configuracoes", "Configurações"),
    ],
)
def test_all_primary_and_personal_pages_render(client, path, label):
    response = client.get(path)
    assert response.status_code == 200
    assert label in response.get_data(as_text=True)
