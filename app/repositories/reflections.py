REFLECTION_FIELDS = """
    reflections.id,
    reflections.title,
    reflections.reflection_text,
    reflections.simple_message,
    reflections.practical_application,
    reflections.prayer_text,
    reflections.reflection_question,
    reflections.author,
    reflections.published_at,
    daily_readings.reading_date,
    daily_readings.start_verse_number,
    daily_readings.end_verse_number,
    books.name_pt AS book_name,
    chapters.chapter_number,
    translations.name AS translation_name,
    translations.attribution_text
"""

REFLECTION_JOINS = """
    FROM reflections
    JOIN daily_readings ON daily_readings.id = reflections.daily_reading_id
    JOIN translations ON translations.id = daily_readings.translation_id
    JOIN chapters ON chapters.id = daily_readings.chapter_id
    JOIN books ON books.id = chapters.book_id
"""

PUBLISHED_AND_COMPLETE = """
    reflections.status = 'published'
    AND translations.license_status = 'approved'
    AND reflections.published_at IS NOT NULL
    AND (
        SELECT COUNT(*)
        FROM verses
        WHERE verses.translation_id = daily_readings.translation_id
          AND verses.chapter_id = daily_readings.chapter_id
          AND verses.verse_number BETWEEN daily_readings.start_verse_number
                                      AND daily_readings.end_verse_number
    ) = daily_readings.end_verse_number - daily_readings.start_verse_number + 1
"""


def get_published_reflection_for_reading(connection, daily_reading_id):
    return connection.execute(
        f"""
        SELECT {REFLECTION_FIELDS}
        {REFLECTION_JOINS}
        WHERE {PUBLISHED_AND_COMPLETE}
          AND daily_readings.id = ?
        """,
        (daily_reading_id,),
    ).fetchone()


def get_published_reflection(connection, reflection_id):
    return connection.execute(
        f"""
        SELECT {REFLECTION_FIELDS}
        {REFLECTION_JOINS}
        WHERE {PUBLISHED_AND_COMPLETE}
          AND reflections.id = ?
        """,
        (reflection_id,),
    ).fetchone()


def count_published_reflections(connection):
    return connection.execute(
        f"""
        SELECT COUNT(*)
        {REFLECTION_JOINS}
        WHERE {PUBLISHED_AND_COMPLETE}
        """
    ).fetchone()[0]


def list_published_reflections(connection, limit, offset):
    return connection.execute(
        f"""
        SELECT {REFLECTION_FIELDS}
        {REFLECTION_JOINS}
        WHERE {PUBLISHED_AND_COMPLETE}
        ORDER BY daily_readings.reading_date DESC, reflections.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
