import unicodedata


def list_translations(connection):
    return connection.execute(
        """
        SELECT code, name, attribution_text, license_name, license_url,
               license_scope, license_evidence_url
        FROM translations
        WHERE license_status = 'approved'
          AND EXISTS (
              SELECT 1
              FROM verses
              WHERE verses.translation_id = translations.id
          )
        ORDER BY name, code
        """
    ).fetchall()


def list_books(connection, translation_code):
    return connection.execute(
        """
        SELECT DISTINCT books.code, books.name_pt, books.canonical_order, books.testament
        FROM books
        JOIN chapters ON chapters.book_id = books.id
        JOIN verses ON verses.chapter_id = chapters.id
        JOIN translations ON translations.id = verses.translation_id
        WHERE translations.code = ? AND translations.license_status = 'approved'
        ORDER BY books.canonical_order
        """,
        (translation_code,),
    ).fetchall()


def get_book_chapters(connection, translation_code, book_code):
    return connection.execute(
        """
        SELECT chapters.chapter_number, COUNT(verses.id) AS verse_count
        FROM translations
        JOIN verses ON verses.translation_id = translations.id
        JOIN chapters ON chapters.id = verses.chapter_id
        JOIN books ON books.id = chapters.book_id
        WHERE translations.code = ?
          AND translations.license_status = 'approved'
          AND books.code = ?
        GROUP BY chapters.id, chapters.chapter_number
        ORDER BY chapters.chapter_number
        """,
        (translation_code, book_code),
    ).fetchall()


def list_chapter_verses(connection, translation_code, book_code, chapter_number):
    return connection.execute(
        """
        SELECT verses.id AS verse_id, verses.verse_number, verses.text,
               books.code AS book_code, books.name_pt AS book_name,
               chapters.chapter_number, translations.code AS translation_code,
               translations.name AS translation_name,
               translations.attribution_text
        FROM verses
        JOIN chapters ON chapters.id = verses.chapter_id
        JOIN books ON books.id = chapters.book_id
        JOIN translations ON translations.id = verses.translation_id
        WHERE translations.code = ?
          AND translations.license_status = 'approved'
          AND books.code = ?
          AND chapters.chapter_number = ?
        ORDER BY verses.verse_number
        """,
        (translation_code, book_code, chapter_number),
    ).fetchall()


def list_verse_notes(connection, verse_ids):
    if not verse_ids:
        return []
    placeholders = ",".join("?" for _ in verse_ids)
    return connection.execute(
        f"""
        SELECT verse_id, note_type, ordinal, note_text
        FROM verse_notes
        WHERE verse_id IN ({placeholders})
        ORDER BY verse_id, note_type, ordinal
        """,
        tuple(verse_ids),
    ).fetchall()


def get_verse(connection, translation_code, book_code, chapter_number, verse_number):
    return connection.execute(
        """
        SELECT verses.id AS verse_id, verses.verse_number, verses.text,
               books.code AS book_code, books.name_pt AS book_name,
               chapters.chapter_number, translations.code AS translation_code,
               translations.name AS translation_name
        FROM verses
        JOIN chapters ON chapters.id = verses.chapter_id
        JOIN books ON books.id = chapters.book_id
        JOIN translations ON translations.id = verses.translation_id
        WHERE translations.code = ?
          AND translations.license_status = 'approved'
          AND books.code = ?
          AND chapters.chapter_number = ?
          AND verses.verse_number = ?
        """,
        (translation_code, book_code, chapter_number, verse_number),
    ).fetchone()


def search_verses(connection, translation_code, query, limit=50):
    term = f"%{query}%"
    rows = connection.execute(
        """
        SELECT verses.id AS verse_id, verses.verse_number, verses.text,
               books.code AS book_code, books.name_pt AS book_name,
               chapters.chapter_number, translations.code AS translation_code,
               translations.name AS translation_name
        FROM verses
        JOIN chapters ON chapters.id = verses.chapter_id
        JOIN books ON books.id = chapters.book_id
        JOIN translations ON translations.id = verses.translation_id
        WHERE translations.code = ?
          AND translations.license_status = 'approved'
          AND verses.text LIKE ? COLLATE NOCASE
        ORDER BY books.canonical_order, chapters.chapter_number,
                 verses.verse_number
        LIMIT ?
        """,
        (translation_code, term, limit),
    ).fetchall()
    if rows:
        return rows

    normalized_query = _normalize_search_text(query)

    candidates = connection.execute(
        """
        SELECT verses.id AS verse_id, verses.verse_number, verses.text,
               books.code AS book_code, books.name_pt AS book_name,
               chapters.chapter_number, translations.code AS translation_code,
               translations.name AS translation_name
        FROM verses
        JOIN chapters ON chapters.id = verses.chapter_id
        JOIN books ON books.id = chapters.book_id
        JOIN translations ON translations.id = verses.translation_id
        WHERE translations.code = ?
          AND translations.license_status = 'approved'
        ORDER BY books.canonical_order, chapters.chapter_number,
                 verses.verse_number
        """,
        (translation_code,),
    ).fetchall()

    return [
        row
        for row in candidates
        if normalized_query in _normalize_search_text(row["text"])
    ][:limit]


def _normalize_search_text(value):
    return "".join(
        character
        for character in unicodedata.normalize("NFD", value.casefold())
        if unicodedata.category(character) != "Mn"
    )


def list_daily_reading_translations(connection, reading_date):
    return connection.execute(
        """
        SELECT translations.code, translations.name, translations.attribution_text
        FROM daily_readings
        JOIN translations ON translations.id = daily_readings.translation_id
        WHERE daily_readings.reading_date = ?
          AND translations.license_status = 'approved'
        ORDER BY translations.name, translations.code
        """,
        (reading_date,),
    ).fetchall()


def get_daily_reading(connection, reading_date, translation_code):
    reading = connection.execute(
        """
        SELECT daily_readings.id AS daily_reading_id,
               daily_readings.start_verse_number,
               daily_readings.end_verse_number,
               daily_readings.translation_id,
               daily_readings.chapter_id,
               books.code AS book_code,
               books.name_pt AS book_name,
               chapters.chapter_number,
               translations.code AS translation_code,
               translations.name AS translation_name,
               translations.attribution_text
        FROM daily_readings
        JOIN translations ON translations.id = daily_readings.translation_id
        JOIN chapters ON chapters.id = daily_readings.chapter_id
        JOIN books ON books.id = chapters.book_id
        WHERE daily_readings.reading_date = ?
          AND translations.code = ?
          AND translations.license_status = 'approved'
        """,
        (reading_date, translation_code),
    ).fetchone()
    if reading is None:
        return None

    verses = connection.execute(
        """
        SELECT id AS verse_id, verse_number, text
        FROM verses
        WHERE translation_id = ?
          AND chapter_id = ?
          AND verse_number BETWEEN ? AND ?
        ORDER BY verse_number
        """,
        (
            reading["translation_id"],
            reading["chapter_id"],
            reading["start_verse_number"],
            reading["end_verse_number"],
        ),
    ).fetchall()

    has_complete_range = (
        len(verses)
        == reading["end_verse_number"] - reading["start_verse_number"] + 1
        and verses[0]["verse_number"] == reading["start_verse_number"]
        and verses[-1]["verse_number"] == reading["end_verse_number"]
        and all(
            current["verse_number"] == previous["verse_number"] + 1
            for previous, current in zip(verses, verses[1:])
        )
    )
    if not has_complete_range:
        return None

    return {"reading": reading, "verses": verses}
