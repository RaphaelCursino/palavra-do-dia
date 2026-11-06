from datetime import datetime, timezone

import pytest

from app.db import get_db
from app.repositories.scripture import (
    get_daily_reading,
    list_daily_reading_translations,
)
from app.services.daily_reading import (
    format_date_pt,
    format_reference,
    local_today,
)


def test_local_today_uses_configured_timezone_at_day_boundary():
    instant = datetime(2026, 1, 1, 2, 30, tzinfo=timezone.utc)

    assert local_today("America/Sao_Paulo", instant) == datetime(2025, 12, 31).date()


def test_local_today_rejects_naive_instants():
    with pytest.raises(ValueError, match="precisa ter fuso"):
        local_today("America/Sao_Paulo", datetime(2026, 1, 1))


def test_invalid_timezone_is_reported():
    with pytest.raises(ValueError, match="IANA inválido"):
        local_today("Invalid/Timezone")


def test_date_and_reference_are_formatted_in_portuguese():
    assert format_date_pt(datetime(2026, 6, 15).date()) == (
        "segunda-feira, 15 de junho de 2026"
    )
    assert format_reference(
        {
            "book_name": "Gênesis",
            "chapter_number": 1,
            "start_verse_number": 1,
            "end_verse_number": 1,
        }
    ) == "Gênesis 1:1"
    assert format_reference(
        {
            "book_name": "Gênesis",
            "chapter_number": 1,
            "start_verse_number": 1,
            "end_verse_number": 2,
        }
    ) == "Gênesis 1:1–2"


def test_daily_reading_lookup_is_date_and_translation_specific(
    app, daily_reading_factory
):
    daily_reading_factory("2026-06-15", "demo-a")
    daily_reading_factory("2026-06-15", "demo-b")

    with app.app_context():
        connection = get_db()
        options = list_daily_reading_translations(connection, "2026-06-15")
        selected = get_daily_reading(connection, "2026-06-15", "demo-b")
        wrong_date = get_daily_reading(connection, "2026-06-16", "demo-b")
        wrong_translation = get_daily_reading(
            connection, "2026-06-15", "not-available"
        )

    assert [option["code"] for option in options] == ["demo-a", "demo-b"]
    assert selected["reading"]["translation_code"] == "demo-b"
    assert selected["verses"][0]["text"] == (
        "TEXTO SINTÉTICO demo-b — NÃO É BÍBLIA."
    )
    assert wrong_date is None
    assert wrong_translation is None


def test_incomplete_verse_range_is_not_returned_as_a_reading(
    app, demo_payload, daily_reading_factory
):
    demo_payload["books"][0]["chapters"][0]["verses"][1]["number"] = 3
    daily_reading_factory(
        "2026-06-15",
        "demo-gap",
        payload=demo_payload,
        start_verse=1,
        end_verse=3,
    )

    with app.app_context():
        reading = get_daily_reading(get_db(), "2026-06-15", "demo-gap")

    assert reading is None
