from .scripture import get_daily_reading



def get_favoriteable_verse(connection, daily_reading_id, verse_number):
    reading_reference = connection.execute(
        """
        SELECT daily_readings.reading_date, translations.code
        FROM daily_readings
        JOIN translations ON translations.id = daily_readings.translation_id
        WHERE daily_readings.id = ?
          AND translations.license_status = 'approved'
        """,
        (daily_reading_id,),
    ).fetchone()
    if reading_reference is None:
        return None

    daily_reading = get_daily_reading(
        connection,
        reading_reference["reading_date"],
        reading_reference["code"],
    )
    if daily_reading is None or daily_reading["reading"]["daily_reading_id"] != daily_reading_id:
        return None

    verse = next(
        (
            row
            for row in daily_reading["verses"]
            if row["verse_number"] == verse_number
        ),
        None,
    )
    if verse is None:
        return None

    reading = daily_reading["reading"]
    return {
        "translation_id": reading["translation_id"],
        "translation_name": reading["translation_name"],
        "chapter_id": reading["chapter_id"],
        "verse_number": verse["verse_number"],
        "verse_text": verse["text"],
        "reference_text": (
            f'{reading["book_name"]} {reading["chapter_number"]}:'
            f'{verse["verse_number"]}'
        ),
    }


def get_favoriteable_verse_by_reference(
    connection,
    translation_code,
    book_code,
    chapter_number,
    verse_number,
):
    row = connection.execute(
        """
        SELECT translations.id AS translation_id,
               translations.name AS translation_name,
               translations.attribution_text,
               chapters.id AS chapter_id,
               books.name_pt AS book_name,
               chapters.chapter_number,
               verses.verse_number,
               verses.text AS verse_text
        FROM translations
        JOIN verses ON verses.translation_id = translations.id
        JOIN chapters ON chapters.id = verses.chapter_id
        JOIN books ON books.id = chapters.book_id
        WHERE translations.code = ?
          AND translations.license_status = 'approved'
          AND books.code = ?
          AND chapters.chapter_number = ?
          AND verses.verse_number = ?
        """,
        (translation_code, book_code, chapter_number, verse_number),
    ).fetchone()
    if row is None:
        return None
    verse = dict(row)
    verse["reference_text"] = (
        f'{verse["book_name"]} {verse["chapter_number"]}:'
        f'{verse["verse_number"]}'
    )
    return verse


def add_verse_favorite(connection, verse, owner_key):
    cursor = connection.execute(
        """
        INSERT INTO favorites (
            owner_key, favorite_type, translation_id, chapter_id,
            verse_number, verse_text, reference_text
        ) VALUES (?, 'verse', ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        (
            owner_key,
            verse["translation_id"],
            verse["chapter_id"],
            verse["verse_number"],
            verse["verse_text"],
            verse["reference_text"],
        ),
    )
    return cursor.rowcount == 1


def add_reflection_favorite(connection, reflection_id, owner_key):
    cursor = connection.execute(
        """
        INSERT INTO favorites (owner_key, favorite_type, reflection_id)
        VALUES (?, 'reflection', ?)
        ON CONFLICT DO NOTHING
        """,
        (owner_key, reflection_id),
    )
    return cursor.rowcount == 1


def remove_favorite(connection, favorite_id, owner_key):
    cursor = connection.execute(
        "DELETE FROM favorites WHERE id = ? AND owner_key = ?",
        (favorite_id, owner_key),
    )
    return cursor.rowcount == 1


def get_favorite_type(connection, favorite_id, owner_key):
    row = connection.execute(
        "SELECT favorite_type FROM favorites WHERE id = ? AND owner_key = ?",
        (favorite_id, owner_key),
    ).fetchone()
    return row["favorite_type"] if row else None


def list_favorites(
    connection,
    owner_key,
    favorite_type=None,
    highlight_category=None,
    highlight_color=None,
):
    verse_favorites = []
    reflection_favorites = []

    if favorite_type in (None, "verse"):
        verse_favorites = connection.execute(
            """
            SELECT favorites.id AS favorite_id,
                   favorites.favorite_type,
                   favorites.reference_text,
                   favorites.verse_text,
                   translations.name AS translation_name,
                   translations.attribution_text,
                   verse_highlights.color AS highlight_color,
                   verse_highlights.category AS highlight_category,
                   favorites.created_at AS favorited_at
            FROM favorites
            JOIN translations ON translations.id = favorites.translation_id
            JOIN chapters ON chapters.id = favorites.chapter_id
            JOIN verses ON verses.translation_id = favorites.translation_id
                       AND verses.chapter_id = favorites.chapter_id
                       AND verses.verse_number = favorites.verse_number
            LEFT JOIN verse_highlights
              ON verse_highlights.favorite_id = favorites.id
            WHERE favorites.owner_key = ?
              AND favorites.favorite_type = 'verse'
              AND translations.license_status = 'approved'
              AND (? IS NULL OR verse_highlights.category = ?)
              AND (? IS NULL OR verse_highlights.color = ?)
            ORDER BY favorites.created_at DESC, favorites.id DESC
            """,
            (
                owner_key,
                highlight_category,
                highlight_category,
                highlight_color,
                highlight_color,
            ),
        ).fetchall()

    if favorite_type in (None, "reflection") and not (
        highlight_category or highlight_color
    ):
        reflection_favorites = connection.execute(
            """
            SELECT favorites.id AS favorite_id,
                   favorites.favorite_type,
                   reflections.id AS reflection_id,
                   reflections.title,
                   reflections.status,
                   reflections.published_at,
                   daily_readings.reading_date,
                   daily_readings.start_verse_number,
                   daily_readings.end_verse_number,
                   books.name_pt AS book_name,
                   chapters.chapter_number,
                   translations.license_status,
                   favorites.created_at AS favorited_at
            FROM favorites
            JOIN reflections ON reflections.id = favorites.reflection_id
            JOIN daily_readings
              ON daily_readings.id = reflections.daily_reading_id
            JOIN translations ON translations.id = daily_readings.translation_id
            JOIN chapters ON chapters.id = daily_readings.chapter_id
            JOIN books ON books.id = chapters.book_id
            WHERE favorites.owner_key = ?
              AND favorites.favorite_type = 'reflection'
              AND reflections.status = 'published'
              AND reflections.published_at IS NOT NULL
              AND translations.license_status = 'approved'
              AND (
                  SELECT COUNT(*)
                  FROM verses
                  WHERE verses.translation_id = daily_readings.translation_id
                    AND verses.chapter_id = daily_readings.chapter_id
                    AND verses.verse_number BETWEEN daily_readings.start_verse_number
                                                AND daily_readings.end_verse_number
              ) = daily_readings.end_verse_number
                  - daily_readings.start_verse_number + 1
            ORDER BY favorites.created_at DESC, favorites.id DESC
            """,
            (owner_key,),
        ).fetchall()

    return [*verse_favorites, *reflection_favorites]


def list_verse_favorite_ids_for_reading(
    connection, daily_reading_id, owner_key
):
    return {
        row["verse_number"]: row["id"]
        for row in connection.execute(
            """
            SELECT favorites.id, favorites.verse_number
            FROM favorites
            JOIN daily_readings
              ON daily_readings.translation_id = favorites.translation_id
             AND daily_readings.chapter_id = favorites.chapter_id
             AND favorites.verse_number BETWEEN daily_readings.start_verse_number
                                           AND daily_readings.end_verse_number
            WHERE daily_readings.id = ?
              AND favorites.owner_key = ?
              AND favorites.favorite_type = 'verse'
            """,
            (daily_reading_id, owner_key),
        ).fetchall()
    }


def list_verse_favorite_ids_for_chapter(
    connection,
    translation_code,
    book_code,
    chapter_number,
    owner_key,
):
    return {
        row["verse_number"]: row["id"]
        for row in connection.execute(
            """
            SELECT favorites.id, favorites.verse_number
            FROM favorites
            JOIN translations ON translations.id = favorites.translation_id
            JOIN chapters ON chapters.id = favorites.chapter_id
            JOIN books ON books.id = chapters.book_id
            WHERE favorites.owner_key = ?
              AND favorites.favorite_type = 'verse'
              AND translations.code = ?
              AND translations.license_status = 'approved'
              AND books.code = ?
              AND chapters.chapter_number = ?
            """,
            (owner_key, translation_code, book_code, chapter_number),
        ).fetchall()
    }


def get_reflection_favorite_id(
        connection, reflection_id, owner_key
):
        row = connection.execute(
            """
            SELECT id FROM favorites
            WHERE owner_key = ?
              AND favorite_type = 'reflection'
              AND reflection_id = ?
            """,
            (owner_key, reflection_id),
        ).fetchone()
        return row["id"] if row else None
