from .scripture import get_daily_reading



def get_reading_for_completion(connection, reading_date, translation_code):
    daily_reading = get_daily_reading(connection, reading_date, translation_code)
    if daily_reading is None:
        return None
    return daily_reading["reading"]


def add_completion(
    connection,
    daily_reading_id,
    completed_date,
    owner_key,
):
    cursor = connection.execute(
        """
        INSERT INTO reading_completions (
            owner_key, daily_reading_id, completed_date
        ) VALUES (?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        (owner_key, daily_reading_id, completed_date),
    )
    return cursor.rowcount == 1


def remove_completion(connection, completion_id, owner_key):
    cursor = connection.execute(
        "DELETE FROM reading_completions WHERE id = ? AND owner_key = ?",
        (completion_id, owner_key),
    )
    return cursor.rowcount == 1


def get_completion_for_date(connection, completed_date, owner_key):
    return connection.execute(
        """
        SELECT id, daily_reading_id, completed_date, completed_at
        FROM reading_completions
        WHERE completed_date = ? AND owner_key = ?
        """,
        (completed_date, owner_key),
    ).fetchone()


def get_completion_id_for_reading_date(
    connection, daily_reading_id, completed_date, owner_key
):
    row = connection.execute(
        """
        SELECT id FROM reading_completions
        WHERE daily_reading_id = ?
          AND completed_date = ?
          AND owner_key = ?
        """,
        (daily_reading_id, completed_date, owner_key),
    ).fetchone()
    return row["id"] if row else None


def list_completion_dates(
    connection, start_date, end_date, owner_key
):
    return [
        row["completed_date"]
        for row in connection.execute(
            """
            SELECT completed_date
            FROM reading_completions
            WHERE owner_key = ?
              AND completed_date BETWEEN ? AND ?
            ORDER BY completed_date
            """,
            (owner_key, start_date, end_date),
        ).fetchall()
    ]


def list_completion_history(connection, owner_key):
    return connection.execute(
        """
        SELECT reading_completions.id AS completion_id,
               reading_completions.completed_date,
               reading_completions.completed_at,
               daily_readings.reading_date,
               daily_readings.start_verse_number,
               daily_readings.end_verse_number,
               books.name_pt AS book_name,
               chapters.chapter_number,
               translations.name AS translation_name
        FROM reading_completions
        JOIN daily_readings ON daily_readings.id = reading_completions.daily_reading_id
        JOIN translations ON translations.id = daily_readings.translation_id
        JOIN chapters ON chapters.id = daily_readings.chapter_id
        JOIN books ON books.id = chapters.book_id
        WHERE reading_completions.owner_key = ?
          AND translations.license_status = 'approved'
          AND (
              SELECT COUNT(*)
              FROM verses
              WHERE verses.translation_id = daily_readings.translation_id
                AND verses.chapter_id = daily_readings.chapter_id
                AND verses.verse_number BETWEEN daily_readings.start_verse_number
                                            AND daily_readings.end_verse_number
          ) = daily_readings.end_verse_number - daily_readings.start_verse_number + 1
        ORDER BY reading_completions.completed_date DESC,
                 reading_completions.completed_at DESC,
                 reading_completions.id DESC
        """,
        (owner_key,),
    ).fetchall()


def get_weekly_goal(connection, owner_key):
    row = connection.execute(
        "SELECT weekly_target FROM reading_goals WHERE owner_key = ?",
        (owner_key,),
    ).fetchone()
    return row["weekly_target"] if row else None


def save_weekly_goal(connection, weekly_target, owner_key):
    connection.execute(
        """
        INSERT INTO reading_goals (owner_key, weekly_target)
        VALUES (?, ?)
        ON CONFLICT(owner_key) DO UPDATE SET
            weekly_target = excluded.weekly_target,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (owner_key, weekly_target),
    )

