
def get_annotation_for_verse(connection, verse_id, owner_key):
    return connection.execute(
        """
        SELECT id, verse_id, note_text, created_at, updated_at
        FROM verse_annotations
        WHERE verse_id = ? AND owner_key = ?
        """,
        (verse_id, owner_key),
    ).fetchone()


def list_annotations(connection, owner_key):
    return connection.execute(
        """
        SELECT verse_annotations.id, verse_annotations.verse_id,
               verse_annotations.note_text, verse_annotations.created_at,
               verse_annotations.updated_at, translations.code AS translation_code,
               translations.name AS translation_name, books.code AS book_code,
               books.name_pt AS book_name, chapters.chapter_number,
               verses.verse_number
        FROM verse_annotations
        JOIN verses ON verses.id = verse_annotations.verse_id
        JOIN chapters ON chapters.id = verses.chapter_id
        JOIN books ON books.id = chapters.book_id
        JOIN translations ON translations.id = verses.translation_id
        WHERE verse_annotations.owner_key = ?
          AND translations.license_status = 'approved'
        ORDER BY books.canonical_order, chapters.chapter_number,
                 verses.verse_number, translations.code
        """,
        (owner_key,),
    ).fetchall()


def list_annotations_for_chapter(
    connection, translation_code, book_code, chapter_number,
    owner_key,
):
    return {
        row["verse_number"]: dict(row)
        for row in connection.execute(
            """
            SELECT verse_annotations.id, verse_annotations.verse_id,
                   verse_annotations.note_text, verses.verse_number
            FROM verse_annotations
            JOIN verses ON verses.id = verse_annotations.verse_id
            JOIN chapters ON chapters.id = verses.chapter_id
            JOIN books ON books.id = chapters.book_id
            JOIN translations ON translations.id = verses.translation_id
            WHERE verse_annotations.owner_key = ?
              AND translations.code = ?
              AND translations.license_status = 'approved'
              AND books.code = ?
              AND chapters.chapter_number = ?
            """,
            (owner_key, translation_code, book_code, chapter_number),
        ).fetchall()
    }


def save_annotation(connection, verse_id, note_text, owner_key):
    connection.execute(
        """
        INSERT INTO verse_annotations (owner_key, verse_id, note_text)
        VALUES (?, ?, ?)
        ON CONFLICT(owner_key, verse_id) DO UPDATE SET
            note_text = excluded.note_text,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (owner_key, verse_id, note_text),
    )
    return get_annotation_for_verse(connection, verse_id, owner_key)


def delete_annotation(connection, annotation_id, owner_key):
    cursor = connection.execute(
        "DELETE FROM verse_annotations WHERE id = ? AND owner_key = ?",
        (annotation_id, owner_key),
    )
    return cursor.rowcount == 1


def get_highlights_for_chapter(
    connection, translation_code, book_code, chapter_number,
    owner_key,
):
    return {
        row["verse_number"]: dict(row)
        for row in connection.execute(
            """
            SELECT favorites.verse_number, favorites.id AS favorite_id,
                   verse_highlights.color, verse_highlights.category
            FROM verse_highlights
            JOIN favorites ON favorites.id = verse_highlights.favorite_id
            JOIN translations ON translations.id = favorites.translation_id
            JOIN chapters ON chapters.id = favorites.chapter_id
            JOIN books ON books.id = chapters.book_id
            WHERE favorites.owner_key = ?
              AND favorites.favorite_type = 'verse'
              AND translations.license_status = 'approved'
              AND translations.code = ?
              AND books.code = ?
              AND chapters.chapter_number = ?
            """,
            (owner_key, translation_code, book_code, chapter_number),
        ).fetchall()
    }


def save_highlight(connection, favorite_id, color, category):
    connection.execute(
        """
        INSERT INTO verse_highlights (favorite_id, color, category)
        VALUES (?, ?, ?)
        ON CONFLICT(favorite_id) DO UPDATE SET
            color = excluded.color,
            category = excluded.category,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (favorite_id, color, category),
    )


def delete_highlight(connection, favorite_id, owner_key):
    cursor = connection.execute(
        """
        DELETE FROM verse_highlights
        WHERE favorite_id = ?
          AND EXISTS (
              SELECT 1 FROM favorites
              WHERE favorites.id = verse_highlights.favorite_id
                AND favorites.owner_key = ?
                AND favorites.favorite_type = 'verse'
          )
        """,
        (favorite_id, owner_key),
    )
    return cursor.rowcount == 1


def list_prayer_entries(connection, owner_key, status=None, entry_type=None):
    conditions = ["owner_key = ?"]
    parameters = [owner_key]
    if status is not None:
        conditions.append("status = ?")
        parameters.append(status)
    if entry_type is not None:
        conditions.append("entry_type = ?")
        parameters.append(entry_type)
    return connection.execute(
        f"""
        SELECT id, entry_type, title, description, status,
               created_at, updated_at, answered_at
        FROM prayer_entries
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC, id DESC
        """,
        parameters,
    ).fetchall()


def get_prayer_entry(connection, entry_id, owner_key):
    return connection.execute(
        """
        SELECT id, entry_type, title, description, status,
               created_at, updated_at, answered_at
        FROM prayer_entries
        WHERE id = ? AND owner_key = ?
        """,
        (entry_id, owner_key),
    ).fetchone()


def create_prayer_entry(connection, entry_type, title, description, owner_key):
    cursor = connection.execute(
        """
        INSERT INTO prayer_entries (owner_key, entry_type, title, description)
        VALUES (?, ?, ?, ?)
        """,
        (owner_key, entry_type, title, description),
    )
    return cursor.lastrowid


def update_prayer_entry(connection, entry_id, entry_type, title, description, owner_key):
    cursor = connection.execute(
        """
        UPDATE prayer_entries
        SET entry_type = ?, title = ?, description = ?,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ? AND owner_key = ? AND status = 'active'
        """,
        (entry_type, title, description, entry_id, owner_key),
    )
    return cursor.rowcount == 1


def delete_prayer_entry(connection, entry_id, owner_key):
    cursor = connection.execute(
        "DELETE FROM prayer_entries WHERE id = ? AND owner_key = ?",
        (entry_id, owner_key),
    )
    return cursor.rowcount == 1


def mark_prayer_answered(connection, entry_id, owner_key):
    cursor = connection.execute(
        """
        UPDATE prayer_entries
        SET status = 'answered',
            answered_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ? AND owner_key = ?
          AND entry_type = 'request' AND status = 'active'
        """,
        (entry_id, owner_key),
    )
    return cursor.rowcount == 1


def get_reader_preferences(connection, owner_key):
    row = connection.execute(
        """
        SELECT theme, font_scale, verse_spacing
        FROM reader_preferences WHERE owner_key = ?
        """,
        (owner_key,),
    ).fetchone()
    return dict(row) if row else {
        "theme": "light",
        "font_scale": 100,
        "verse_spacing": "comfortable",
    }


def save_reader_preferences(
    connection, theme, font_scale, verse_spacing, owner_key
):
    connection.execute(
        """
        INSERT INTO reader_preferences (owner_key, theme, font_scale, verse_spacing)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(owner_key) DO UPDATE SET
            theme = excluded.theme,
            font_scale = excluded.font_scale,
            verse_spacing = excluded.verse_spacing,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (owner_key, theme, font_scale, verse_spacing),
    )
