import sqlite3

import pytest

from app.db import get_db


def test_progress_schema_enforces_references_uniqueness_and_goal_range(
    app, daily_reading_factory
):
    daily_reading_factory("2026-06-15", "demo-progress-a")
    daily_reading_factory("2026-06-15", "demo-progress-b")

    with app.app_context():
        connection = get_db()
        reading_ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM daily_readings ORDER BY id"
            ).fetchall()
        ]
        connection.execute(
            """
            INSERT INTO reading_completions (
                owner_key, daily_reading_id, completed_date
            ) VALUES ('local-installation', ?, '2026-06-15')
            """,
            (reading_ids[0],),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO reading_completions (
                    owner_key, daily_reading_id, completed_date
                ) VALUES ('local-installation', ?, '2026-06-15')
                """,
                (reading_ids[1],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO reading_completions (
                    owner_key, daily_reading_id, completed_date
                ) VALUES ('local-installation', 999999, '2026-06-16')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO reading_goals (owner_key, weekly_target)
                VALUES ('local-installation', 0)
                """
            )
        connection.execute(
            """
            INSERT INTO reading_goals (owner_key, weekly_target)
            VALUES ('local-installation', 5)
            """
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
