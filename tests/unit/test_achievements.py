from datetime import date, timedelta

from app.db import get_db
from app.services.achievements import sync_achievements


def _reading_id(app, reading_date):
    with app.app_context():
        return get_db().execute(
            "SELECT id FROM daily_readings WHERE reading_date = ?",
            (reading_date,),
        ).fetchone()["id"]


def test_achievements_use_real_dates_and_do_not_count_one_chapter_twice(
    app, daily_reading_factory
):
    for index, reading_date in enumerate(("2026-06-13", "2026-06-14", "2026-06-15")):
        daily_reading_factory(reading_date, translation_code=f"demo-test-{index}")

    with app.app_context():
        connection = get_db()
        for reading_date in ("2026-06-13", "2026-06-14", "2026-06-15"):
            connection.execute(
                """
                INSERT INTO reading_completions (daily_reading_id, completed_date)
                VALUES (?, ?)
                """,
                (_reading_id(app, reading_date), reading_date),
            )
        achievements = sync_achievements(
            connection, "local-installation", date(2026, 6, 15)
        )
        connection.commit()
        by_code = {item["code"]: item for item in achievements}

        assert by_code["first-reading"]["unlocked"]
        assert by_code["streak-3"]["unlocked"]
        assert by_code["chapters-10"]["value"] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM achievement_unlocks"
        ).fetchone()[0] >= 2


def test_achievement_unlocks_are_persistent_and_reversible(app, daily_reading_factory):
    daily_reading_factory("2026-06-15")
    with app.app_context():
        connection = get_db()
        connection.execute(
            """
            INSERT INTO reading_completions (daily_reading_id, completed_date)
            VALUES (?, '2026-06-15')
            """,
            (_reading_id(app, "2026-06-15"),),
        )
        sync_achievements(connection, "local-installation", date(2026, 6, 15))
        connection.commit()
        assert connection.execute(
            "SELECT COUNT(*) FROM achievement_unlocks WHERE achievement_code = 'first-reading'"
        ).fetchone()[0] == 1

        connection.execute("DELETE FROM reading_completions")
        sync_achievements(connection, "local-installation", date(2026, 6, 15))
        connection.commit()
        assert connection.execute(
            "SELECT COUNT(*) FROM achievement_unlocks"
        ).fetchone()[0] == 0


def test_all_threshold_groups_unlock_from_their_realistic_aggregates(app, monkeypatch):
    from app.services import achievements as achievement_service

    dates = [
        (date(2026, 1, 1) + timedelta(days=day)).isoformat()
        for day in range(52)
    ]
    monkeypatch.setattr(
        achievement_service,
        "_completion_facts",
        lambda _connection, _owner: (dates, 500, 10),
    )
    monkeypatch.setattr(
        achievement_service,
        "_goal_weeks",
        lambda _connection, _owner, _target: {
            f"2026-{week:02d}-05": set() for week in range(1, 13)
        },
    )
    with app.app_context():
        connection = get_db()
        connection.execute(
            "INSERT INTO reading_goals (weekly_target) VALUES (1)"
        )
        achievements = achievement_service.sync_achievements(
            connection, "local-installation", date(2026, 2, 21)
        )
        unlocked = {item["code"] for item in achievements if item["unlocked"]}
        assert {
            "first-reading", "streak-3", "streak-7", "streak-15", "streak-30",
            "chapters-10", "chapters-50", "chapters-100", "chapters-500",
            "books-1", "books-5", "books-10", "goals-1", "goals-4", "goals-12",
        } <= unlocked
