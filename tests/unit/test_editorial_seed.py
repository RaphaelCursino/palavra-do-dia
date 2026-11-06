import copy
from pathlib import Path

import pytest

from app.db import get_db
from app.services.blivre_import import import_blivre_archive
from app.services.editorial_seed import (
    EditorialSeedError,
    load_initial_editorial,
    publish_initial_editorial,
)
from app.services.scripture_import import LicenseApproval


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "data" / "imports" / "blivre" / "porbr2018_usfm.zip"
EDITORIAL = ROOT / "data" / "editorial" / "blivre-initial-week.json"
EXPECTED_REFERENCES = [
    "Salmos 23:1–3",
    "Provérbios 3:5–6",
    "Mateus 6:33",
    "João 15:4–5",
    "Efésios 2:8–10",
    "Romanos 12:1–2",
    "Filipenses 4:6–7",
]


def _import_blivre(connection):
    return import_blivre_archive(
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


def test_initial_week_uses_existing_blivre_verses_and_publishes_idempotently(app):
    payload = load_initial_editorial(EDITORIAL)

    with app.app_context():
        connection = get_db()
        _import_blivre(connection)
        first = publish_initial_editorial(connection, payload)
        repeated = publish_initial_editorial(connection, payload)

        readings = connection.execute(
            """
            SELECT daily_readings.id, daily_readings.reading_date,
                   books.code AS book_code, chapters.chapter_number,
                   daily_readings.start_verse_number,
                   daily_readings.end_verse_number
            FROM daily_readings
            JOIN translations ON translations.id = daily_readings.translation_id
            JOIN chapters ON chapters.id = daily_readings.chapter_id
            JOIN books ON books.id = chapters.book_id
            WHERE translations.code = 'blivre'
            ORDER BY daily_readings.reading_date
            """
        ).fetchall()
        reflections = connection.execute(
            """
            SELECT reflections.*, daily_readings.reading_date
            FROM reflections
            JOIN daily_readings ON daily_readings.id = reflections.daily_reading_id
            JOIN translations ON translations.id = daily_readings.translation_id
            WHERE translations.code = 'blivre'
            ORDER BY daily_readings.reading_date
            """
        ).fetchall()
        verse_texts = []
        for reading in readings:
            verse_texts.append(
                connection.execute(
                    """
                    SELECT verses.verse_number, verses.text
                    FROM verses
                    JOIN translations ON translations.id = verses.translation_id
                    JOIN chapters ON chapters.id = verses.chapter_id
                    JOIN books ON books.id = chapters.book_id
                    WHERE translations.code = 'blivre'
                      AND books.code = ?
                      AND chapters.chapter_number = ?
                      AND verses.verse_number BETWEEN ? AND ?
                    ORDER BY verses.verse_number
                    """,
                    (
                        reading["book_code"],
                        reading["chapter_number"],
                        reading["start_verse_number"],
                        reading["end_verse_number"],
                    ),
                ).fetchall()
            )
        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

    assert first["reading_count"] == 7
    assert first["reflection_count"] == 7
    assert first["inserted_reading_count"] == 7
    assert first["inserted_reflection_count"] == 7
    assert first["references"] == EXPECTED_REFERENCES
    assert repeated["already_published"] is True
    assert repeated["inserted_reading_count"] == 0
    assert repeated["inserted_reflection_count"] == 0
    assert [reading["reading_date"] for reading in readings] == [
        f"2026-11-{day:02d}" for day in range(5, 12)
    ]
    assert len(reflections) == 7
    assert all(reflection["status"] == "published" for reflection in reflections)
    assert [len(verses) for verses in verse_texts] == [3, 2, 1, 2, 3, 2, 2]
    assert all(all(verse["text"].strip() for verse in verses) for verses in verse_texts)
    assert foreign_key_violations == []


def test_missing_blivre_reference_rolls_back_the_entire_seed(app):
    payload = load_initial_editorial(EDITORIAL)
    payload["days"][3]["reference"]["book_code"] = "ZZZ"

    with app.app_context():
        connection = get_db()
        _import_blivre(connection)
        with pytest.raises(EditorialSeedError, match="inexistente ou incompleta"):
            publish_initial_editorial(connection, payload)
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_readings"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM reflections"
        ).fetchone()[0] == 0


def test_existing_editorial_conflict_is_never_overwritten(app):
    payload = load_initial_editorial(EDITORIAL)
    conflicting = copy.deepcopy(payload)
    conflicting["days"][0]["reflection_text"] += " Texto alterado."

    with app.app_context():
        connection = get_db()
        _import_blivre(connection)
        publish_initial_editorial(connection, payload)
        with pytest.raises(EditorialSeedError, match="reflexão diferente"):
            publish_initial_editorial(connection, conflicting)
        stored = connection.execute(
            "SELECT reflection_text FROM reflections ORDER BY id LIMIT 1"
        ).fetchone()[0]

    assert stored == payload["days"][0]["reflection_text"]


def test_seed_requires_exactly_seven_consecutive_days():
    payload = load_initial_editorial(EDITORIAL)
    payload["days"].pop()

    with pytest.raises(EditorialSeedError, match="exatamente 7 dias"):
        publish_initial_editorial(None, payload)
