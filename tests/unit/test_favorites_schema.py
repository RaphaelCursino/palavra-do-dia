import sqlite3

import pytest

from app.db import get_db


def test_favorite_schema_rejects_invalid_shapes_and_nonexistent_scripture(
    app, daily_reading_factory
):
    daily_reading_factory("2026-06-15")
    with app.app_context():
        connection = get_db()
        translation = connection.execute(
            "SELECT id FROM translations WHERE code = 'demo-test'"
        ).fetchone()["id"]
        chapter = connection.execute(
            "SELECT id FROM chapters WHERE chapter_number = 1"
        ).fetchone()["id"]

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO favorites (
                    favorite_type, translation_id, chapter_id, verse_number,
                    verse_text, reference_text
                ) VALUES ('verse', ?, ?, 99, 'texto', 'Gênesis 1:99')
                """,
                (translation, chapter),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO favorites (
                    favorite_type, translation_id, chapter_id, verse_number,
                    verse_text, reference_text
                ) VALUES ('reflection', ?, ?, 1, 'texto', 'Gênesis 1:1')
                """,
                (translation, chapter),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO favorites (owner_key, favorite_type, reflection_id)
                VALUES ('   ', 'reflection', 1)
                """
            )


def test_database_constraints_prevent_duplicate_favorites(app, reflection_factory):
    reflection_factory("2026-06-15")
    with app.app_context():
        connection = get_db()
        verse = connection.execute(
            """
            SELECT translations.id AS translation_id, chapters.id AS chapter_id,
                   verses.verse_number, verses.text
            FROM verses
            JOIN translations ON translations.id = verses.translation_id
            JOIN chapters ON chapters.id = verses.chapter_id
            WHERE translations.code = 'demo-reflection' AND verses.verse_number = 1
            """
        ).fetchone()
        reflection_id = connection.execute(
            "SELECT id FROM reflections"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO favorites (
                favorite_type, translation_id, chapter_id, verse_number,
                verse_text, reference_text
            ) VALUES ('verse', ?, ?, ?, ?, 'Gênesis 1:1')
            """,
            (
                verse["translation_id"],
                verse["chapter_id"],
                verse["verse_number"],
                verse["text"],
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO favorites (
                    favorite_type, translation_id, chapter_id, verse_number,
                    verse_text, reference_text
                ) VALUES ('verse', ?, ?, ?, ?, 'Gênesis 1:1')
                """,
                (
                    verse["translation_id"],
                    verse["chapter_id"],
                    verse["verse_number"],
                    verse["text"],
                ),
            )
        connection.rollback()

        connection.execute(
            "INSERT INTO favorites (favorite_type, reflection_id) VALUES ('reflection', ?)",
            (reflection_id,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO favorites (favorite_type, reflection_id) VALUES ('reflection', ?)",
                (reflection_id,),
            )


def test_favorite_owner_key_scopes_read_and_remove_operations(
    app, daily_reading_factory
):
    from app.repositories.favorites import (
        add_verse_favorite,
        get_favoriteable_verse,
        list_favorites,
        remove_favorite,
    )

    daily_reading_factory("2026-06-15")
    with app.app_context():
        connection = get_db()
        reading_id = connection.execute(
            "SELECT id FROM daily_readings"
        ).fetchone()["id"]
        verse = get_favoriteable_verse(connection, reading_id, 1)
        assert add_verse_favorite(connection, verse, "account:sample-user")
        connection.commit()

        assert len(list_favorites(connection, owner_key="account:sample-user")) == 1
        assert list_favorites(connection, owner_key="local-installation") == []
        favorite_id = connection.execute(
            "SELECT id FROM favorites"
        ).fetchone()["id"]
        assert not remove_favorite(
            connection, favorite_id, owner_key="local-installation"
        )
        assert remove_favorite(
            connection, favorite_id, owner_key="account:sample-user"
        )


def test_favorite_foreign_keys_restrict_deleting_source_verse_and_reflection(
    app, reflection_factory
):
    reflection_factory("2026-06-15")
    with app.app_context():
        connection = get_db()
        verse = connection.execute(
            """
            SELECT translations.id AS translation_id, chapters.id AS chapter_id,
                   verses.verse_number, verses.text, verses.id AS verse_id
            FROM verses
            JOIN translations ON translations.id = verses.translation_id
            JOIN chapters ON chapters.id = verses.chapter_id
            WHERE translations.code = 'demo-reflection' AND verses.verse_number = 1
            """
        ).fetchone()
        reflection_id = connection.execute(
            "SELECT id FROM reflections"
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO favorites (favorite_type, reflection_id) VALUES ('reflection', ?)",
            (reflection_id,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM reflections WHERE id = ?", (reflection_id,)
            )
        connection.rollback()

        connection.execute(
            """
            INSERT INTO favorites (
                favorite_type, translation_id, chapter_id, verse_number,
                verse_text, reference_text
            ) VALUES ('verse', ?, ?, ?, ?, 'Gênesis 1:1')
            """,
            (
                verse["translation_id"],
                verse["chapter_id"],
                verse["verse_number"],
                verse["text"],
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM verses WHERE id = ?", (verse["verse_id"],))
