import sqlite3

import pytest

from app.db import get_db


def test_reflection_schema_rejects_empty_content_and_invalid_publication_state(
    app, daily_reading_factory
):
    daily_reading_factory("2026-06-15")

    with app.app_context():
        connection = get_db()
        reading_id = connection.execute(
            "SELECT id FROM daily_readings"
        ).fetchone()["id"]

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO reflections (
                    daily_reading_id, title, reflection_text, simple_message,
                    practical_application, prayer_text, reflection_question,
                    author, status, published_at
                ) VALUES (?, '', 'texto', 'mensagem', 'aplicação', 'oração',
                          'pergunta', 'autoria', 'published', '2026-06-15')
                """,
                (reading_id,),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO reflections (
                    daily_reading_id, title, reflection_text, simple_message,
                    practical_application, prayer_text, reflection_question,
                    author, status
                ) VALUES (?, 'Título', 'texto', 'mensagem', 'aplicação', 'oração',
                          'pergunta', 'autoria', 'published')
                """,
                (reading_id,),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO reflections (
                    daily_reading_id, title, reflection_text, simple_message,
                    practical_application, prayer_text, reflection_question,
                    author, status
                ) VALUES (?, 'Título', 'texto', 'mensagem', 'aplicação', 'oração',
                          'pergunta', 'autoria', 'unreviewed')
                """,
                (reading_id,),
            )


def test_one_reflection_is_allowed_per_daily_reading(app, reflection_factory):
    reflection_factory("2026-06-15")

    with app.app_context():
        connection = get_db()
        reading_id = connection.execute(
            "SELECT id FROM daily_readings"
        ).fetchone()["id"]

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO reflections (
                    daily_reading_id, title, reflection_text, simple_message,
                    practical_application, prayer_text, reflection_question,
                    author, status
                ) VALUES (?, 'Outro título', 'texto', 'mensagem', 'aplicação',
                          'oração', 'pergunta', 'autoria', 'draft')
                """,
                (reading_id,),
            )
