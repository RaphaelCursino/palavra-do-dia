import json
from datetime import date
from pathlib import Path

from .blivre_import import BLIVRE_NAME
from ..db import begin_transaction


EDITORIAL_FIELDS = (
    "title",
    "reflection_text",
    "simple_message",
    "practical_application",
    "prayer_text",
    "reflection_question",
)
EDITORIAL_AUTHOR = "author"
TRANSLATION_CODE = "blivre"
LICENSE_NAME = "Creative Commons Atribuição 4.0 Brasil (CC BY 4.0)"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/br/"
SEED_VERSION = 1


class EditorialSeedError(ValueError):
    """Editorial seed data is invalid, conflicting, or cannot be published."""


def load_initial_editorial(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise EditorialSeedError(
            f"Não foi possível ler o arquivo editorial: {path}"
        ) from error
    except json.JSONDecodeError as error:
        raise EditorialSeedError(
            f"Arquivo editorial JSON inválido: {path}"
        ) from error


def publish_initial_editorial(connection, payload):
    days = _validate_payload(payload)
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        begin_transaction(connection)
    else:
        connection.execute("SAVEPOINT publish_initial_editorial")
    succeeded = False
    try:
        translation = connection.execute(
            """
            SELECT id, name, license_name, license_url
            FROM translations
            WHERE code = ? AND license_status = 'approved'
            """,
            (TRANSLATION_CODE,),
        ).fetchone()
        if (
            translation is None
            or translation["name"] != BLIVRE_NAME
            or translation["license_name"] != LICENSE_NAME
            or translation["license_url"] != LICENSE_URL
        ):
            raise EditorialSeedError(
                "A tradução BLIVRE aprovada com licença CC BY 4.0 não está "
                "disponível no banco."
            )

        prepared = [
            _prepare_day(connection, translation["id"], day)
            for day in days
        ]
        inserted_readings = 0
        inserted_reflections = 0
        for item in prepared:
            reading_id = item["reading_id"]
            if reading_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO daily_readings (
                        reading_date, translation_id, chapter_id,
                        start_verse_number, end_verse_number
                    ) VALUES (?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        item["reading_date"],
                        translation["id"],
                        item["chapter_id"],
                        item["start_verse_number"],
                        item["end_verse_number"],
                    ),
                )
                reading_id = cursor.fetchone()["id"]
                inserted_readings += 1

            if not item["reflection_exists"]:
                fields = item["content"]
                connection.execute(
                    """
                    INSERT INTO reflections (
                        daily_reading_id, title, reflection_text, simple_message,
                        practical_application, prayer_text, reflection_question,
                        author, status, published_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'published', ?)
                    """,
                    (
                        reading_id,
                        fields["title"],
                        fields["reflection_text"],
                        fields["simple_message"],
                        fields["practical_application"],
                        fields["prayer_text"],
                        fields["reflection_question"],
                        fields[EDITORIAL_AUTHOR],
                        f'{item["reading_date"]}T12:00:00Z',
                    ),
                )
                inserted_reflections += 1

        if owns_transaction:
            connection.commit()
        else:
            connection.execute("RELEASE SAVEPOINT publish_initial_editorial")
        succeeded = True
    finally:
        if not succeeded:
            if owns_transaction:
                connection.rollback()
            else:
                connection.execute("ROLLBACK TO SAVEPOINT publish_initial_editorial")
                connection.execute("RELEASE SAVEPOINT publish_initial_editorial")

    return {
        "reading_count": len(prepared),
        "reflection_count": len(prepared),
        "inserted_reading_count": inserted_readings,
        "inserted_reflection_count": inserted_reflections,
        "already_published": inserted_readings == 0 and inserted_reflections == 0,
        "references": [item["reference"] for item in prepared],
    }


def _validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) != {
        "format_version",
        "translation_code",
        "author",
        "days",
    }:
        raise EditorialSeedError("Estrutura do pacote editorial inválida.")
    if (
        type(payload["format_version"]) is not int
        or payload["format_version"] != SEED_VERSION
    ):
        raise EditorialSeedError("Versão do pacote editorial não suportada.")
    if payload["translation_code"] != TRANSLATION_CODE:
        raise EditorialSeedError("O conteúdo inicial deve usar somente BLIVRE.")
    author = _nonempty(payload["author"], "author")
    days = payload["days"]
    if not isinstance(days, list) or len(days) != 7:
        raise EditorialSeedError("O pacote inicial deve conter exatamente 7 dias.")

    validated = []
    seen_dates = set()
    seen_references = set()
    first_date = None
    for index, day in enumerate(days):
        if not isinstance(day, dict) or set(day) != {
            "reading_date",
            "reference",
            *EDITORIAL_FIELDS,
        }:
            raise EditorialSeedError(f"Conteúdo editorial do dia {index + 1} inválido.")
        raw_date = day["reading_date"]
        if not isinstance(raw_date, str):
            raise EditorialSeedError(f"Data inválida no dia {index + 1}.")
        try:
            reading_date = date.fromisoformat(raw_date)
        except ValueError as error:
            raise EditorialSeedError(
                f"Data inválida no dia {index + 1}; use AAAA-MM-DD."
            ) from error
        if reading_date.isoformat() != raw_date:
            raise EditorialSeedError(
                f"Data inválida no dia {index + 1}; use AAAA-MM-DD."
            )
        if raw_date in seen_dates:
            raise EditorialSeedError(f"Data editorial duplicada: {raw_date}.")
        if first_date is None:
            first_date = reading_date
        elif reading_date.toordinal() != first_date.toordinal() + index:
            raise EditorialSeedError(
                "As sete datas editoriais precisam ser consecutivas."
            )
        seen_dates.add(raw_date)

        reference = day["reference"]
        if not isinstance(reference, dict) or set(reference) != {
            "book_code",
            "chapter_number",
            "start_verse_number",
            "end_verse_number",
        }:
            raise EditorialSeedError(f"Referência inválida no dia {index + 1}.")
        book_code = _nonempty(reference["book_code"], "reference.book_code")
        chapter_number = _positive_int(
            reference["chapter_number"],
            "reference.chapter_number",
        )
        start_verse = _positive_int(
            reference["start_verse_number"],
            "reference.start_verse_number",
        )
        end_verse = _positive_int(
            reference["end_verse_number"],
            "reference.end_verse_number",
        )
        if end_verse < start_verse:
            raise EditorialSeedError(
                f"Intervalo de versículos invertido no dia {index + 1}."
            )
        reference_key = (book_code, chapter_number, start_verse, end_verse)
        if reference_key in seen_references:
            raise EditorialSeedError("Referência duplicada no pacote editorial.")
        seen_references.add(reference_key)

        content = {
            field: _nonempty(day[field], field)
            for field in EDITORIAL_FIELDS
        }
        content[EDITORIAL_AUTHOR] = author
        validated.append(
            {
                "reading_date": raw_date,
                "book_code": book_code,
                "chapter_number": chapter_number,
                "start_verse_number": start_verse,
                "end_verse_number": end_verse,
                "content": content,
            }
        )
    return validated


def _prepare_day(connection, translation_id, day):
    reference = day
    rows = connection.execute(
        """
        SELECT chapters.id AS chapter_id, books.name_pt AS book_name,
               verses.verse_number
        FROM translations
        JOIN verses ON verses.translation_id = translations.id
        JOIN chapters ON chapters.id = verses.chapter_id
        JOIN books ON books.id = chapters.book_id
        WHERE translations.id = ?
          AND translations.license_status = 'approved'
          AND books.code = ?
          AND chapters.chapter_number = ?
          AND verses.verse_number BETWEEN ? AND ?
        ORDER BY verses.verse_number
        """,
        (
            translation_id,
            reference["book_code"],
            reference["chapter_number"],
            reference["start_verse_number"],
            reference["end_verse_number"],
        ),
    ).fetchall()
    expected_numbers = list(
        range(reference["start_verse_number"], reference["end_verse_number"] + 1)
    )
    if (
        len(rows) != len(expected_numbers)
        or [row["verse_number"] for row in rows] != expected_numbers
    ):
        raise EditorialSeedError(
            f'Referência BLIVRE inexistente ou incompleta: '
            f'{reference["book_code"]} {reference["chapter_number"]}:'
            f'{reference["start_verse_number"]}-{reference["end_verse_number"]}.'
        )

    existing_reading = connection.execute(
        """
        SELECT id, chapter_id, start_verse_number, end_verse_number
        FROM daily_readings
        WHERE reading_date = ? AND translation_id = ?
        """,
        (day["reading_date"], translation_id),
    ).fetchone()
    if existing_reading is not None and (
        existing_reading["chapter_id"] != rows[0]["chapter_id"]
        or existing_reading["start_verse_number"] != day["start_verse_number"]
        or existing_reading["end_verse_number"] != day["end_verse_number"]
    ):
        raise EditorialSeedError(
            f'Já existe outra leitura BLIVRE para {day["reading_date"]}; '
            "nenhum registro foi alterado."
        )

    reflection_exists = False
    if existing_reading is not None:
        existing_reflection = connection.execute(
            """
            SELECT title, reflection_text, simple_message,
                   practical_application, prayer_text, reflection_question,
                   author, status, published_at
            FROM reflections
            WHERE daily_reading_id = ?
            """,
            (existing_reading["id"],),
        ).fetchone()
        if existing_reflection is not None:
            expected = {
                **day["content"],
                "status": "published",
                "published_at": f'{day["reading_date"]}T12:00:00Z',
            }
            if any(
                existing_reflection[field] != expected[field]
                for field in (*EDITORIAL_FIELDS, "author", "status", "published_at")
            ):
                raise EditorialSeedError(
                    f'Já existe uma reflexão diferente para {day["reading_date"]}; '
                    "nenhum registro foi alterado."
                )
            reflection_exists = True

    return {
        **day,
        "chapter_id": rows[0]["chapter_id"],
        "reading_id": existing_reading["id"] if existing_reading else None,
        "reflection_exists": reflection_exists,
        "reference": _format_seed_reference(day, rows[0]["book_name"]),
    }


def _format_seed_reference(day, book_name):
    reference = (
        f'{book_name} {day["chapter_number"]}:'
        f'{day["start_verse_number"]}'
    )
    if day["end_verse_number"] != day["start_verse_number"]:
        reference += f'–{day["end_verse_number"]}'
    return reference


def _nonempty(value, field):
    if not isinstance(value, str) or not value.strip():
        raise EditorialSeedError(f"Campo obrigatório ausente ou inválido: {field}.")
    return value.strip()


def _positive_int(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EditorialSeedError(f"{field} deve ser um inteiro positivo.")
    return value
