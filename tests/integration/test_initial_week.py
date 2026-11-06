from datetime import date
from pathlib import Path

from app.db import get_db
from app.services.blivre_import import import_blivre_archive
from app.services.editorial_seed import (
    load_initial_editorial,
    publish_initial_editorial,
)
from app.services.scripture_import import LicenseApproval


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "data" / "imports" / "blivre" / "porbr2018_usfm.zip"
EDITORIAL = ROOT / "data" / "editorial" / "blivre-initial-week.json"


def _publish_week(app):
    with app.app_context():
        connection = get_db()
        import_blivre_archive(
            connection,
            ARCHIVE,
            LicenseApproval(
                reviewed_by="Testes automatizados",
                reviewed_at="2026-11-05",
                evidence_url="https://ebible.org/find/details.php?id=porbr2018",
                confirmed=True,
            ),
            "2026-11-05",
        )
        connection.commit()
        return publish_initial_editorial(
            connection,
            load_initial_editorial(EDITORIAL),
        )


def test_initial_week_home_history_and_favorite_use_blivre_records(
    app, client, monkeypatch
):
    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 11, 5))
    result = _publish_week(app)

    with app.app_context():
        connection = get_db()
        reading = connection.execute(
            """
            SELECT daily_readings.id AS reading_id, reflections.id AS reflection_id
            FROM daily_readings
            JOIN translations ON translations.id = daily_readings.translation_id
            JOIN reflections ON reflections.daily_reading_id = daily_readings.id
            WHERE translations.code = 'blivre'
              AND daily_readings.reading_date = '2026-11-05'
            """
        ).fetchone()
        verse_text = connection.execute(
            """
            SELECT verses.text
            FROM daily_readings
            JOIN translations ON translations.id = daily_readings.translation_id
            JOIN verses ON verses.translation_id = translations.id
                AND verses.chapter_id = daily_readings.chapter_id
                AND verses.verse_number = daily_readings.start_verse_number
            WHERE daily_readings.id = ?
            """,
            (reading["reading_id"],),
        ).fetchone()["text"]

    home = client.get("/")
    home_html = home.get_data(as_text=True)
    home_text = home_html
    history = client.get("/reflexoes")
    history_html = history.get_data(as_text=True)

    assert result["reading_count"] == 7
    assert result["reflection_count"] == 7
    assert home.status_code == 200
    assert "Palavra do Dia" in home_text
    assert "Salmos 23:1–3" in home_text
    assert verse_text in home_text
    assert "Reflexão do dia" in home_text
    assert "Cuidado que restaura e conduz" in home_text
    assert "Para levar à vida" in home_text
    assert "Oração do dia" in home_text
    assert "Para refletir" in home_text
    assert "Bíblia Livre (BLIVRE)" in home_text
    assert "Copyright © 2018 Diego Santos, Mario Sérgio e Marco Teles" in home_text
    assert 'data-share-reference="Salmos 23:1–3"' in home_html
    assert "data-share-button" in home_html
    assert "data-copy-prayer" in home_html
    assert history.status_code == 200
    assert history_html.count('class="history-card"') == 7
    for reference in (
        "Salmos 23:1–3",
        "Provérbios 3:5–6",
        "Mateus 6:33",
        "João 15:4–5",
        "Efésios 2:8–10",
        "Romanos 12:1–2",
        "Filipenses 4:6–7",
    ):
        assert reference in history_html

    verse_favorite = client.post(
        "/favoritos/versiculo",
        data={
            "csrf_token": app.config["FAVORITES_CSRF_TOKEN"],
            "daily_reading_id": reading["reading_id"],
            "verse_number": "1",
            "next": "/",
        },
    )
    reflection_favorite = client.post(
        "/favoritos/reflexao",
        data={
            "csrf_token": app.config["FAVORITES_CSRF_TOKEN"],
            "reflection_id": reading["reflection_id"],
            "next": "/",
        },
    )
    favorites = client.get("/favoritos")
    favorites_html = favorites.get_data(as_text=True)

    assert verse_favorite.status_code == 302
    assert reflection_favorite.status_code == 302
    assert favorites.status_code == 200
    assert verse_text in favorites_html
    assert "Cuidado que restaura e conduz" in favorites_html


def test_initial_week_cli_requires_explicit_publish_confirmation(app):
    result = app.test_cli_runner().invoke(args=["publish-initial-editorial"])

    assert result.exit_code != 0
    assert "--confirm-publish" in result.output
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM daily_readings"
        ).fetchone()[0] == 0


def test_initial_week_cli_publishes_all_seven_days_once(app):
    with app.app_context():
        connection = get_db()
        import_blivre_archive(
            connection,
            ARCHIVE,
            LicenseApproval(
                reviewed_by="Testes automatizados",
                reviewed_at="2026-11-05",
                evidence_url="https://ebible.org/find/details.php?id=porbr2018",
                confirmed=True,
            ),
            "2026-11-05",
        )
        connection.commit()

    runner = app.test_cli_runner()
    first = runner.invoke(args=["publish-initial-editorial", "--confirm-publish"])
    second = runner.invoke(args=["publish-initial-editorial", "--confirm-publish"])

    assert first.exit_code == 0, first.output
    assert "7 leituras, 7 reflexões" in first.output
    assert "7 leituras e 7 reflexões adicionadas" in first.output
    assert second.exit_code == 0, second.output
    assert "Semana editorial já estava publicada" in second.output
    assert "0 leituras e 0 reflexões adicionadas" in second.output
    with app.app_context():
        connection = get_db()
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_readings"
        ).fetchone()[0] == 7
        assert connection.execute(
            "SELECT COUNT(*) FROM reflections WHERE status = 'published'"
        ).fetchone()[0] == 7
