def test_card_reference_uses_approved_blivre_text_and_rejects_invalid_reference(
    app, client, monkeypatch
):
    from datetime import date
    from pathlib import Path

    from app.db import get_db
    from app.services.blivre_import import import_blivre_archive
    from app.services.scripture_import import LicenseApproval
    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    with app.app_context():
        connection = get_db()
        import_blivre_archive(
            connection,
            (
                Path(__file__).resolve().parents[2]
                / "data"
                / "imports"
                / "blivre"
                / "porbr2018_usfm.zip"
            ),
            LicenseApproval(
                reviewed_by="Testes",
                reviewed_at="2026-11-05",
                evidence_url="https://ebible.org/find/details.php?id=porbr2018",
                confirmed=True,
            ),
            "2026-11-05",
        )
        connection.commit()

    valid = client.get("/biblia/cartao?translation=blivre&referencia=Jo%C3%A3o+3%3A16")
    page = valid.get_data(as_text=True)
    assert valid.status_code == 200
    assert "Porque Deus amou ao mundo" in page
    assert "BLIVRE/porbr2018" in page
    assert "data-verse-card-canvas" in page
    assert 'data-card-download' in page

    invalid = client.get("/biblia/cartao?translation=blivre&referencia=Jo%C3%A3o+999%3A999")
    assert invalid.status_code == 200
    assert "Informe uma referência válida" in invalid.get_data(as_text=True)


def test_achievements_page_is_available_after_a_real_completion(
    app, client, daily_reading_factory, monkeypatch
):
    from datetime import date
    from app.db import get_db
    from app.routes import pages

    monkeypatch.setattr(pages, "local_today", lambda _timezone: date(2026, 6, 15))
    daily_reading_factory("2026-06-15")
    with app.app_context():
        connection = get_db()
        reading_id = connection.execute(
            "SELECT id FROM daily_readings WHERE reading_date = '2026-06-15'"
        ).fetchone()["id"]
    response = client.post(
        "/progresso/concluir",
        data={
            "csrf_token": app.config["PROGRESS_CSRF_TOKEN"],
            "daily_reading_id": reading_id,
        },
    )
    assert response.status_code == 302
    page = client.get("/conquistas")
    assert page.status_code == 200
    assert "Primeira leitura" in page.get_data(as_text=True)
    assert "desbloqueadas" in page.get_data(as_text=True)
