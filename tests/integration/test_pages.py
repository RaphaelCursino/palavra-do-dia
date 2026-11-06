from app.services.daily_reading import format_date_pt, local_today


def test_home_page_shows_beautiful_empty_reading_without_inventing_scripture(client):
    response = client.get("/")
    page = response.get_data(as_text=True)
    expected_date = format_date_pt(local_today("America/Sao_Paulo"))

    assert response.status_code == 200
    assert "Palavra do Dia" in page
    assert "Versículo do Dia" in page
    assert "A leitura de hoje está em preparação" in page
    assert "Ainda não há uma leitura autorizada cadastrada" in page
    assert "Novas reflexões serão disponibilizadas" in page
    assert "Nenhum texto foi inventado" in page
    assert expected_date in page
    assert "Compartilhar" in page
    assert 'disabled title="Compartilhamento disponível' in page
    assert "TEXTO SINTÉTICO" not in page
    for section in ("Início", "Bíblia", "Reflexões", "Favoritos", "Meu progresso"):
        assert section in page


def test_home_page_displays_the_selected_translation_and_reference(
    app, client, daily_reading_factory, monkeypatch
):
    from datetime import date

    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    daily_reading_factory("2026-06-15", "demo-a")
    daily_reading_factory("2026-06-15", "demo-b")

    response = client.get("/?translation=demo-b")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Gênesis 1:1–2" in page
    assert "Demonstração de teste demo-b (NÃO É BÍBLIA)" in page
    assert "TEXTO SINTÉTICO demo-b — NÃO É BÍBLIA." in page
    assert "TEXTO SINTÉTICO demo-a — NÃO É BÍBLIA." not in page
    assert "Compartilhamento disponível" not in page
    assert "data-share-button" in page
    assert 'data-share-attribution="Conteúdo de teste não bíblico."' in page
    assert "Novas reflexões serão disponibilizadas" in page


def test_home_page_displays_only_published_reflection_associated_with_reading(
    app, client, reflection_factory, monkeypatch
):
    from datetime import date

    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    reflection_factory("2026-06-15")

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Reflexão de demonstração demo-reflection (TESTE)" in page
    assert "REFLEXÃO SINTÉTICA — NÃO É CONTEÚDO OFICIAL." in page
    assert "Gênesis 1:1–2 · Demonstração de teste demo-reflection" in page
    assert "MENSAGEM DE TESTE — NÃO É CONTEÚDO OFICIAL." in page
    assert "APLICAÇÃO DE TESTE — NÃO É CONTEÚDO OFICIAL." in page
    assert "ORAÇÃO SINTÉTICA — NÃO É CONTEÚDO OFICIAL." in page
    assert "PERGUNTA DE TESTE — NÃO É CONTEÚDO OFICIAL?" in page
    assert "Autoria de teste" in page
    assert "data-copy-prayer" in page


def test_home_page_does_not_display_draft_reflections(
    app, client, reflection_factory, monkeypatch
):
    from datetime import date

    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    reflection_factory("2026-06-15", status="draft")

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Novas reflexões serão disponibilizadas" in page
    assert "REFLEXÃO SINTÉTICA" not in page


def test_reflection_history_is_empty_until_content_is_published(client):
    response = client.get("/reflexoes")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "O histórico está começando" in page
    assert "Nenhum conteúdo fictício é exibido" in page
    assert "REFLEXÃO SINTÉTICA" not in page


def test_reflection_history_lists_only_published_items_with_scripture_reference(
    client, reflection_factory
):
    reflection_id = reflection_factory("2026-06-15")
    reflection_factory("2026-06-14", "demo-draft", status="draft")

    response = client.get("/reflexoes")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert f"/reflexoes/{reflection_id}" in page
    assert "Reflexão de demonstração demo-reflection (TESTE)" in page
    assert "Gênesis 1:1–2 · Demonstração de teste demo-reflection" in page
    assert "demo-draft" not in page


def test_reflection_detail_displays_all_sections_and_prayer_copy_control(
    client, reflection_factory
):
    reflection_id = reflection_factory("2026-06-15")

    response = client.get(f"/reflexoes/{reflection_id}")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Leitura relacionada" in page
    assert "Gênesis 1:1–2" in page
    assert "Em poucas palavras" in page
    assert "Para levar à vida" in page
    assert "Oração do dia" in page
    assert "Para refletir" in page
    assert "Autoria de teste" in page
    assert "data-copy-prayer" in page
    assert "ORAÇÃO SINTÉTICA — NÃO É CONTEÚDO OFICIAL." in page


def test_reflection_history_and_detail_do_not_expose_draft_or_unknown_items(
    client, reflection_factory
):
    draft_id = reflection_factory("2026-06-15", status="draft")

    assert client.get(f"/reflexoes/{draft_id}").status_code == 404
    assert client.get("/reflexoes/9999").status_code == 404
    assert client.get("/reflexoes?page=0").status_code == 404
    assert client.get("/reflexoes?page=invalid").status_code == 404


def test_reflection_history_paginates_published_content_only(
    client, reflection_factory
):
    reflection_ids = [
        reflection_factory(
            f"2026-06-{day:02d}",
            f"demo-page-{day:02d}",
        )
        for day in range(1, 12)
    ]

    first_page = client.get("/reflexoes")
    second_page = client.get("/reflexoes?page=2")
    first_html = first_page.get_data(as_text=True)
    second_html = second_page.get_data(as_text=True)

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert f"/reflexoes/{reflection_ids[-1]}" in first_html
    assert "Próximas" in first_html
    assert f"/reflexoes/{reflection_ids[0]}" in second_html
    assert "Página 2 de 2" in second_html
    assert "Próximas" not in second_html


def test_reflection_section_asks_for_translation_before_selecting_reflection(
    client, daily_reading_factory, reflection_factory, monkeypatch
):
    from datetime import date

    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    reflection_factory("2026-06-15", "demo-a")
    daily_reading_factory("2026-06-15", "demo-b")

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Escolha uma tradução acima para ver a reflexão" in page
    assert "REFLEXÃO SINTÉTICA" not in page


def test_home_page_requires_translation_choice_when_more_than_one_is_available(
    app, client, daily_reading_factory, monkeypatch
):
    from datetime import date

    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    daily_reading_factory("2026-06-15", "demo-a")
    daily_reading_factory("2026-06-15", "demo-b")

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Escolha uma tradução para consultar" in page
    assert "TEXTO SINTÉTICO" not in page
    assert 'name="translation"' in page


def test_home_page_uses_the_only_available_translation(
    app, client, daily_reading_factory, monkeypatch
):
    from datetime import date

    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    daily_reading_factory("2026-06-15", "demo-only")

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "TEXTO SINTÉTICO demo-only — NÃO É BÍBLIA." in page
    assert 'value="demo-only" selected' in page


def test_home_page_does_not_show_a_reading_for_another_date(
    app, client, daily_reading_factory, monkeypatch
):
    from datetime import date

    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    daily_reading_factory("2026-06-14", "demo-test")

    response = client.get("/?translation=demo-test")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Ainda não há uma leitura diária editorial cadastrada" in page
    assert "TEXTO SINTÉTICO" not in page


def test_home_page_rejects_unavailable_translation_selection(
    app, client, daily_reading_factory, monkeypatch
):
    from datetime import date

    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    daily_reading_factory("2026-06-15", "demo-test")

    response = client.get("/?translation=unknown")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Essa tradução não tem uma leitura autorizada cadastrada" in page
    assert "TEXTO SINTÉTICO" not in page


def test_home_page_hides_incomplete_daily_passage(
    app, client, daily_reading_factory, demo_payload, monkeypatch
):
    from datetime import date

    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    demo_payload["books"][0]["chapters"][0]["verses"][1]["number"] = 3
    daily_reading_factory(
        "2026-06-15",
        "demo-gap",
        payload=demo_payload,
        start_verse=1,
        end_verse=3,
    )

    response = client.get("/?translation=demo-gap")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "A passagem cadastrada não está completa" in page
    assert "TEXTO SINTÉTICO" not in page


def test_unknown_page_returns_a_friendly_404(client):
    response = client.get("/pagina-inexistente")

    assert response.status_code == 404
    assert "Página não encontrada" in response.get_data(as_text=True)
    assert "Palavra do Dia" in response.get_data(as_text=True)


def test_unhandled_error_returns_a_friendly_500(app, client):
    @app.get("/_test/erro")
    def failing_view():
        raise RuntimeError("detalhe interno que não deve ser exposto")

    response = client.get("/_test/erro")
    page = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "Ocorreu um erro interno" in page
    assert "detalhe interno que não deve ser exposto" not in page


def test_database_timeout_error_page_does_not_acquire_another_connection(
    app, client, monkeypatch
):
    from psycopg_pool import PoolTimeout

    @app.get("/_test/database-timeout")
    def database_timeout():
        raise PoolTimeout("connection unavailable")

    monkeypatch.setattr(
        "app.get_reader_preferences",
        lambda *_args: pytest.fail(
            "A página de erro não deve consultar preferências no banco."
        ),
    )

    response = client.get("/_test/database-timeout")
    page = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "Ocorreu um erro interno" in page
