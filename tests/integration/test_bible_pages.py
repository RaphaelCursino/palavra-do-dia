from datetime import date
from pathlib import Path

from app.db import get_db
from app.services.blivre_import import import_blivre_archive
from app.services.scripture_import import LicenseApproval


ARCHIVE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "imports"
    / "blivre"
    / "porbr2018_usfm.zip"
)


def test_bible_navigation_favorites_and_daily_reading_use_imported_blivre(
    app, client, monkeypatch
):
    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    approval = LicenseApproval(
        reviewed_by="Solicitação do usuário",
        reviewed_at="2026-11-05",
        evidence_url="https://ebible.org/find/details.php?id=porbr2018",
        confirmed=True,
    )
    with app.app_context():
        connection = get_db()
        result = import_blivre_archive(
            connection,
            ARCHIVE_PATH,
            approval,
            "2026-11-05",
        )
        assert result["verse_count"] == 31102
        connection.commit()
        empty_home = client.get("/").get_data(as_text=True)
        assert 'value="blivre" selected' in empty_home
        assert "leitura diária editorial cadastrada" in empty_home
        assert "No princípio criou Deus" not in empty_home
        genesis = connection.execute(
            """
            SELECT translations.id AS translation_id, chapters.id AS chapter_id
            FROM translations
            JOIN chapters ON chapters.book_id = (
                SELECT id FROM books WHERE code = 'GEN'
            )
            WHERE translations.code = 'blivre' AND chapters.chapter_number = 1
            """
        ).fetchone()
        connection.execute(
            """
            INSERT INTO daily_readings (
                reading_date, translation_id, chapter_id,
                start_verse_number, end_verse_number
            ) VALUES ('2026-06-15', ?, ?, 1, 1)
            """,
            (genesis["translation_id"], genesis["chapter_id"]),
        )
        connection.commit()

    bible = client.get("/biblia")
    bible_page = bible.get_data(as_text=True)
    book = client.get("/biblia/blivre/GEN")
    book_page = book.get_data(as_text=True)
    chapter = client.get("/biblia/blivre/GEN/1")
    chapter_page = chapter.get_data(as_text=True)

    assert bible.status_code == 200
    assert 'value="blivre" selected' in bible_page
    assert "Bíblia Livre (BLIVRE)" in bible_page
    assert bible_page.count('class="bible-book-card"') == 66
    assert 'aria-label="Livros do Antigo Testamento"' in bible_page
    assert 'aria-label="Livros do Novo Testamento"' in bible_page
    assert ">39 livros</span>" in bible_page
    assert ">27 livros</span>" in bible_page
    assert 'data-bible-book-search' in bible_page
    assert "Copyright © 2018 Diego Santos, Mario Sérgio e Marco Teles" in bible_page
    assert 'href="/biblia/blivre/GEN"' in bible_page
    assert bible_page.index("Gênesis") < bible_page.index("Êxodo")
    assert book.status_code == 200
    assert book_page.count('class="bible-chapter-card"') == 50
    assert 'href="/biblia/blivre/GEN/1"' in book_page
    assert chapter.status_code == 200
    assert "Gênesis 1" in chapter_page
    assert "No princípio criou Deus os céus e a terra." in chapter_page
    assert "Salvar versículo" in chapter_page
    assert 'data-reader-font="increase"' in chapter_page
    assert 'data-reader-font="decrease"' in chapter_page
    assert 'data-reader-focus' in chapter_page
    assert 'data-share-button' in chapter_page
    assert 'rel="next" href="/biblia/blivre/GEN/2"' in chapter_page
    assert "Fonte oficial" in chapter_page
    assert "creativecommons.org/licenses/by/4.0/br/" in chapter_page
    genesis_last = client.get("/biblia/blivre/GEN/50").get_data(as_text=True)
    assert 'rel="next" href="/biblia/blivre/EXO/1"' in genesis_last
    note_response = client.get("/biblia/blivre/GEN/3")
    chapter_with_note = note_response.get_data(as_text=True)
    assert note_response.status_code == 200
    assert "1 nota" in chapter_with_note
    assert "3:5 Ou: sereis como Deus" in chapter_with_note

    favorite_response = client.post(
        "/favoritos/versiculo",
        data={
            "csrf_token": app.config["FAVORITES_CSRF_TOKEN"],
            "translation_code": "blivre",
            "book_code": "GEN",
            "chapter_number": "1",
            "verse_number": "1",
            "next": "/biblia/blivre/GEN/1",
        },
    )
    assert favorite_response.status_code == 302
    assert favorite_response.headers["Location"].startswith(
        "/biblia/blivre/GEN/1?favorite_status=verse-added"
    )
    chapter_after_favorite = client.get(favorite_response.headers["Location"])
    assert "Remover dos favoritos" in chapter_after_favorite.get_data(as_text=True)

    favorites = client.get("/favoritos?tipo=versiculos")
    favorites_page = favorites.get_data(as_text=True)
    assert "No princípio criou Deus os céus e a terra." in favorites_page
    assert "Bíblia Livre (BLIVRE)" in favorites_page
    assert "Copyright © 2018 Diego Santos, Mario Sérgio e Marco Teles" in favorites_page

    daily = client.get("/")
    daily_page = daily.get_data(as_text=True)
    assert daily.status_code == 200
    assert "Versículo do Dia" in daily_page
    assert "No princípio criou Deus os céus e a terra." in daily_page
    assert "Bíblia Livre (BLIVRE)" in daily_page
    assert "sites.google.com/site/biblialivre/" in daily_page
    assert client.get("/progresso").status_code == 200
    assert client.get("/reflexoes").status_code == 200
    assert client.get("/biblia/blivre/GEN/999").status_code == 404


def test_bible_search_supports_reference_words_empty_and_invalid(
    app, client, daily_reading_factory
):
    daily_reading_factory("2026-06-15", "search-demo")

    reference = client.get(
        "/biblia/busca?q=G%C3%AAnesis+1%3A1&translation=search-demo"
    )
    reference_page = reference.get_data(as_text=True)
    assert reference.status_code == 200
    assert "Gênesis 1:1" in reference_page
    assert 'href="/biblia/search-demo/GEN/1#verse-1"' in reference_page
    assert "TEXTO SINTÉTICO search-demo" in reference_page

    word = client.get(
        "/biblia/busca?q=SINT%C3%89TICO&translation=search-demo"
    )
    word_page = word.get_data(as_text=True)
    assert word.status_code == 200
    assert "resultado" in word_page
    assert "Gênesis 1:1" in word_page

    empty = client.get(
        "/biblia/busca?q=palavra-inexistente&translation=search-demo"
    )
    assert "Nenhum versículo encontrado" in empty.get_data(as_text=True)

    invalid = client.get("/biblia/busca?q=x&translation=search-demo")
    assert "pelo menos 2 caracteres" in invalid.get_data(as_text=True)


def test_bible_chapter_exposes_quick_jumps_and_navigation(client, daily_reading_factory):
    daily_reading_factory("2026-06-15", "jump-demo")

    response = client.get("/biblia/jump-demo/GEN/1")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-bible-book-jump' in page
    assert 'data-bible-chapter-jump' in page
    assert 'href="/biblia/busca?translation=jump-demo"' in page
    assert 'value="/biblia/jump-demo/GEN/1" selected' in page
