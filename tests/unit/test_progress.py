from datetime import date, datetime, timezone

import pytest

from app.services.daily_reading import local_today
from app.services.progress import (
    calculate_progress_metrics,
    calculate_streaks,
    month_days_bounds,
    serialize_month_calendar,
    shift_month,
    validate_month,
)


def test_calendar_serializes_completed_days_and_month_boundary():
    today = date(2026, 6, 15)
    calendar = serialize_month_calendar(
        date(2026, 6, 1),
        ["2026-06-02", "2026-06-15"],
        today,
    )
    days = [day for week in calendar.weeks for day in week]

    assert calendar.title == "Junho 2026"
    assert any(day["date"] == "2026-06-02" and day["completed"] for day in days)
    assert any(day["date"] == "2026-06-15" and day["completed"] for day in days)
    assert calendar.previous_month == "2026-05"
    assert calendar.next_month is None


def test_month_navigation_handles_year_change():
    assert shift_month(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert shift_month(date(2025, 12, 1), 1) == date(2026, 1, 1)
    assert month_days_bounds(date(2024, 2, 1)) == (
        "2024-02-01",
        "2024-02-29",
    )


def test_month_validation_rejects_incomplete_future_and_old_dates():
    today = date(2026, 1, 2)

    assert validate_month(None, None, today) == date(2026, 1, 1)
    assert validate_month("2025", "12", today) == date(2025, 12, 1)
    with pytest.raises(ValueError):
        validate_month("2025", None, today)
    with pytest.raises(ValueError):
        validate_month("2026", "2", today)
    with pytest.raises(ValueError):
        validate_month("1999", "12", today)


def test_current_and_longest_streaks_are_derived_from_recorded_dates():
    current, longest = calculate_streaks(
        [
            "2026-06-10",
            "2026-06-11",
            "2026-06-13",
            "2026-06-14",
            "2026-06-15",
        ],
        date(2026, 6, 15),
    )
    assert current == 3
    assert longest == 3


def test_current_streak_allows_yesterday_but_not_older_activity():
    assert calculate_streaks(["2026-06-14", "2026-06-15"], date(2026, 6, 16))[0] == 2
    assert calculate_streaks(["2026-06-13"], date(2026, 6, 15))[0] == 0


def test_metrics_deduplicate_dates_and_count_week_in_local_calendar():
    history = [
        {"completed_date": "2026-06-15"},
        {"completed_date": "2026-06-15"},
        {"completed_date": "2026-06-16"},
    ]

    assert calculate_progress_metrics(history, date(2026, 6, 16)) == {
        "total_days": 2,
        "current_streak": 2,
        "longest_streak": 2,
        "weekly_total": 2,
    }


def test_sao_paulo_timezone_controls_date_at_utc_day_boundary():
    instant = datetime(2026, 1, 1, 2, 30, tzinfo=timezone.utc)
    assert local_today("America/Sao_Paulo", instant) == date(2025, 12, 31)
