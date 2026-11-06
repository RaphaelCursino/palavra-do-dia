import hashlib
import json

import pytest

from app import create_app
from app.db import get_db
from app.services.scripture_import import import_translation


@pytest.fixture
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "PROPAGATE_EXCEPTIONS": False,
            "DATABASE_PATH": str(tmp_path / "test.sqlite3"),
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def demo_payload():
    return {
        "format_version": 1,
        "translation": {
            "code": "demo-test",
            "name": "Demonstração de teste (NÃO É BÍBLIA)",
            "language_code": "pt-BR",
            "rights_holder": "Dados sintéticos de teste",
            "license": {
                "name": "Licença fictícia de teste",
                "url": "https://example.invalid/license",
                "scope": "Somente dados demonstrativos gerados para testes.",
                "attribution": "Conteúdo de teste não bíblico.",
            },
        },
        "source": {
            "name": "Fixture local de testes",
            "url": "https://example.invalid/source",
            "acquired_at": "2026-01-02",
        },
        "books": [
            {
                "code": "GEN",
                "chapters": [
                    {
                        "number": 1,
                        "verses": [
                            {
                                "number": 1,
                                "text": "TEXTO DE DEMONSTRAÇÃO - NÃO É BÍBLIA.",
                            },
                            {
                                "number": 2,
                                "text": "SEGUNDO TEXTO DE TESTE - NÃO É BÍBLIA.",
                            },
                        ],
                    }
                ],
            }
        ],
    }


@pytest.fixture
def license_approval():
    from app.services.scripture_import import LicenseApproval

    return LicenseApproval(
        reviewed_by="Responsável dos testes",
        reviewed_at="2026-01-03",
        evidence_url="https://example.invalid/test-evidence",
        confirmed=True,
    )


@pytest.fixture
def daily_reading_factory(app, demo_payload, license_approval):
    def add_reading(
        reading_date,
        translation_code="demo-test",
        payload=None,
        start_verse=1,
        end_verse=2,
    ):
        source_payload = json.loads(json.dumps(payload or demo_payload))
        source_payload["translation"]["code"] = translation_code
        source_payload["translation"]["name"] = (
            f"Demonstração de teste {translation_code} (NÃO É BÍBLIA)"
        )
        source_payload["books"][0]["chapters"][0]["verses"][0]["text"] = (
            f"TEXTO SINTÉTICO {translation_code} — NÃO É BÍBLIA."
        )
        source_bytes = json.dumps(
            source_payload,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")

        with app.app_context():
            connection = get_db()
            import_translation(
                connection,
                source_payload,
                license_approval,
                hashlib.sha256(source_bytes).hexdigest(),
            )
            reading = connection.execute(
                """
                SELECT translations.id AS translation_id, chapters.id AS chapter_id
                FROM translations
                JOIN books ON books.code = 'GEN'
                JOIN chapters ON chapters.book_id = books.id
                WHERE translations.code = ? AND chapters.chapter_number = 1
                """,
                (translation_code,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO daily_readings (
                    reading_date, translation_id, chapter_id,
                    start_verse_number, end_verse_number
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    reading_date,
                    reading["translation_id"],
                    reading["chapter_id"],
                    start_verse,
                    end_verse,
                ),
            )
            connection.commit()

        return translation_code

    return add_reading


@pytest.fixture
def reflection_factory(app, daily_reading_factory):
    def add_reflection(
        reading_date,
        translation_code="demo-reflection",
        status="published",
    ):
        daily_reading_factory(reading_date, translation_code)
        published_at = "2026-06-15T12:00:00Z" if status == "published" else None
        with app.app_context():
            connection = get_db()
            reading = connection.execute(
                """
                SELECT daily_readings.id
                FROM daily_readings
                JOIN translations ON translations.id = daily_readings.translation_id
                WHERE daily_readings.reading_date = ?
                  AND translations.code = ?
                """,
                (reading_date, translation_code),
            ).fetchone()
            cursor = connection.execute(
                """
                INSERT INTO reflections (
                    daily_reading_id, title, reflection_text, simple_message,
                    practical_application, prayer_text, reflection_question,
                    author, status, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reading["id"],
                    f"Reflexão de demonstração {translation_code} (TESTE)",
                    "REFLEXÃO SINTÉTICA — NÃO É CONTEÚDO OFICIAL.",
                    "MENSAGEM DE TESTE — NÃO É CONTEÚDO OFICIAL.",
                    "APLICAÇÃO DE TESTE — NÃO É CONTEÚDO OFICIAL.",
                    "ORAÇÃO SINTÉTICA — NÃO É CONTEÚDO OFICIAL.",
                    "PERGUNTA DE TESTE — NÃO É CONTEÚDO OFICIAL?",
                    "Autoria de teste",
                    status,
                    published_at,
                ),
            )
            connection.commit()
            return cursor.lastrowid

    return add_reflection
