import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


IMPORTER_VERSION = "1"
TRANSLATION_CODE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
BOOK_CODE = re.compile(r"^[A-Z0-9]{3}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ScriptureImportError(ValueError):
    """Entrada de importação ausente, inválida ou sem aprovação de licença."""


@dataclass(frozen=True)
class LicenseApproval:
    reviewed_by: str
    reviewed_at: str
    evidence_url: str
    confirmed: bool


def _require_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ScriptureImportError(f"Campo obrigatório ausente ou inválido: {field}")
    return value.strip()


def _require_https_url(value, field):
    url = _require_text(value, field)
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ScriptureImportError(f"{field} deve ser uma URL HTTPS.")
    return url


def _require_iso_date(value, field):
    text = _require_text(value, field)
    if not ISO_DATE.fullmatch(text):
        raise ScriptureImportError(f"{field} deve usar o formato AAAA-MM-DD.")
    try:
        date.fromisoformat(text)
    except ValueError as error:
        raise ScriptureImportError(f"{field} deve usar o formato AAAA-MM-DD.") from error
    return text


def _positive_integer(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ScriptureImportError(f"{field} deve ser um inteiro positivo.")
    return value


def _validate_payload(payload, approval):
    if (
        not isinstance(payload, dict)
        or type(payload.get("format_version")) is not int
        or payload.get("format_version") != 1
    ):
        raise ScriptureImportError("Formato de importação inválido ou não suportado.")
    if not isinstance(approval, LicenseApproval) or approval.confirmed is not True:
        raise ScriptureImportError(
            "Importação bloqueada: confirme a licença somente após verificar "
            "os direitos de armazenamento e exibição."
        )

    translation = payload.get("translation")
    source = payload.get("source")
    books = payload.get("books")
    if not isinstance(translation, dict):
        raise ScriptureImportError("Metadados da tradução inválidos.")
    if not isinstance(source, dict):
        raise ScriptureImportError("Metadados da fonte inválidos.")
    if not isinstance(books, list) or not books:
        raise ScriptureImportError("A importação deve conter ao menos um livro.")

    code = _require_text(translation.get("code"), "translation.code")
    if not TRANSLATION_CODE.fullmatch(code):
        raise ScriptureImportError("translation.code contém caracteres inválidos.")

    license_data = translation.get("license")
    if not isinstance(license_data, dict):
        raise ScriptureImportError("Metadados de licença ausentes.")
    translation_record = {
        "code": code,
        "name": _require_text(translation.get("name"), "translation.name"),
        "language_code": _require_text(
            translation.get("language_code"), "translation.language_code"
        ),
        "rights_holder": _require_text(
            translation.get("rights_holder"), "translation.rights_holder"
        ),
        "license_name": _require_text(
            license_data.get("name"), "translation.license.name"
        ),
        "license_url": _require_https_url(
            license_data.get("url"), "translation.license.url"
        ),
        "license_scope": _require_text(
            license_data.get("scope"), "translation.license.scope"
        ),
        "attribution_text": _require_text(
            license_data.get("attribution"), "translation.license.attribution"
        ),
        "license_reviewed_by": _require_text(
            approval.reviewed_by, "license approval reviewer"
        ),
        "license_reviewed_at": _require_iso_date(
            approval.reviewed_at, "license approval date"
        ),
        "license_evidence_url": _require_https_url(
            approval.evidence_url, "license approval evidence"
        ),
    }

    source_record = {
        "name": _require_text(source.get("name"), "source.name"),
        "url": _require_https_url(source.get("url"), "source.url"),
        "acquired_at": _require_iso_date(source.get("acquired_at"), "source.acquired_at"),
    }

    validated_books = []
    seen_books = set()
    seen_chapters = set()
    verse_count = 0
    note_count = 0
    for book_index, book in enumerate(books):
        field = f"books[{book_index}]"
        if not isinstance(book, dict):
            raise ScriptureImportError(f"{field} deve ser um objeto.")
        book_code = _require_text(book.get("code"), f"{field}.code")
        if not BOOK_CODE.fullmatch(book_code):
            raise ScriptureImportError(f"{field}.code deve ser um código canônico.")
        if book_code in seen_books:
            raise ScriptureImportError(f"Livro duplicado na importação: {book_code}.")
        seen_books.add(book_code)

        chapters = book.get("chapters")
        if not isinstance(chapters, list) or not chapters:
            raise ScriptureImportError(f"{field}.chapters deve conter capítulos.")

        validated_chapters = []
        for chapter_index, chapter in enumerate(chapters):
            chapter_field = f"{field}.chapters[{chapter_index}]"
            if not isinstance(chapter, dict):
                raise ScriptureImportError(f"{chapter_field} deve ser um objeto.")
            chapter_number = _positive_integer(
                chapter.get("number"), f"{chapter_field}.number"
            )
            chapter_key = (book_code, chapter_number)
            if chapter_key in seen_chapters:
                raise ScriptureImportError(
                    f"Capítulo duplicado na importação: {book_code} {chapter_number}."
                )
            seen_chapters.add(chapter_key)

            verses = chapter.get("verses")
            if not isinstance(verses, list) or not verses:
                raise ScriptureImportError(
                    f"{chapter_field}.verses deve conter versículos."
                )
            validated_verses = []
            seen_verses = set()
            for verse_index, verse in enumerate(verses):
                verse_field = f"{chapter_field}.verses[{verse_index}]"
                if not isinstance(verse, dict):
                    raise ScriptureImportError(f"{verse_field} deve ser um objeto.")
                verse_number = _positive_integer(
                    verse.get("number"), f"{verse_field}.number"
                )
                if verse_number in seen_verses:
                    raise ScriptureImportError(
                        f"Versículo duplicado: {book_code} "
                        f"{chapter_number}:{verse_number}."
                    )
                seen_verses.add(verse_number)
                text = _require_text(verse.get("text"), f"{verse_field}.text")
                raw_notes = verse.get("notes", [])
                if not isinstance(raw_notes, list):
                    raise ScriptureImportError(
                        f"{verse_field}.notes deve ser uma lista."
                    )
                validated_notes = []
                for note_index, note in enumerate(raw_notes):
                    note_field = f"{verse_field}.notes[{note_index}]"
                    if not isinstance(note, dict):
                        raise ScriptureImportError(f"{note_field} deve ser um objeto.")
                    note_type = note.get("type")
                    if note_type not in ("footnote", "cross_reference"):
                        raise ScriptureImportError(
                            f"{note_field}.type não é suportado."
                        )
                    validated_notes.append(
                        {
                            "type": note_type,
                            "text": _require_text(note.get("text"), f"{note_field}.text"),
                        }
                    )
                    note_count += 1
                validated_verses.append((verse_number, text, validated_notes))
                verse_count += 1

            validated_chapters.append((chapter_number, validated_verses))
        validated_books.append((book_code, validated_chapters))

    return (
        translation_record,
        source_record,
        validated_books,
        verse_count,
        note_count,
    )


def import_translation(
    connection,
    payload,
    approval,
    source_sha256,
    importer_version=IMPORTER_VERSION,
):
    if not isinstance(source_sha256, str) or not SHA256.fullmatch(source_sha256):
        raise ScriptureImportError("Hash SHA-256 da fonte inválido.")

    translation, source, books, verse_count, note_count = _validate_payload(
        payload,
        approval,
    )
    try:
        with connection:
            existing = connection.execute(
                "SELECT 1 FROM translations WHERE code = ?", (translation["code"],)
            ).fetchone()
            if existing:
                raise ScriptureImportError(
                    f"A tradução {translation['code']} já existe; "
                    "não é permitido sobrescrevê-la."
                )

            cursor = connection.execute(
                """
                INSERT INTO translations (
                    code, name, language_code, rights_holder, license_name,
                    license_url, license_scope, attribution_text,
                    license_reviewed_by, license_reviewed_at, license_evidence_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(translation.values()),
            )
            translation_id = cursor.lastrowid
            chapter_count = sum(len(chapters) for _, chapters in books)
            batch_cursor = connection.execute(
                """
                INSERT INTO import_batches (
                    translation_id, source_name, source_url, acquired_at,
                    source_sha256, importer_version, book_count, chapter_count,
                    verse_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    translation_id,
                    source["name"],
                    source["url"],
                    source["acquired_at"],
                    source_sha256,
                    importer_version,
                    len(books),
                    chapter_count,
                    verse_count,
                ),
            )
            batch_id = batch_cursor.lastrowid

            for book_code, chapters in books:
                book_row = connection.execute(
                    "SELECT id FROM books WHERE code = ?", (book_code,)
                ).fetchone()
                if book_row is None:
                    raise ScriptureImportError(
                        f"Livro não pertence ao cânon configurado: {book_code}."
                    )
                for chapter_number, verses in chapters:
                    connection.execute(
                        """
                        INSERT INTO chapters (book_id, chapter_number)
                        VALUES (?, ?)
                        ON CONFLICT (book_id, chapter_number) DO NOTHING
                        """,
                        (book_row["id"], chapter_number),
                    )
                    chapter_id = connection.execute(
                        "SELECT id FROM chapters WHERE book_id = ? AND chapter_number = ?",
                        (book_row["id"], chapter_number),
                    ).fetchone()["id"]

                    connection.executemany(
                        """
                        INSERT INTO verses (
                            translation_id, chapter_id, verse_number, text,
                            import_batch_id
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        [
                            (translation_id, chapter_id, number, text, batch_id)
                            for number, text, _notes in verses
                        ],
                    )
                    for verse_number, _text, notes in verses:
                        if not notes:
                            continue
                        verse_id = connection.execute(
                            """
                            SELECT id FROM verses
                            WHERE translation_id = ?
                              AND chapter_id = ?
                              AND verse_number = ?
                            """,
                            (translation_id, chapter_id, verse_number),
                        ).fetchone()["id"]
                        connection.executemany(
                            """
                            INSERT INTO verse_notes (
                                verse_id, note_type, ordinal, note_text
                            ) VALUES (?, ?, ?, ?)
                            """,
                            [
                                (verse_id, note["type"], ordinal, note["text"])
                                for ordinal, note in enumerate(notes, start=1)
                            ],
                        )
    except ScriptureImportError:
        raise
    except sqlite3.IntegrityError as error:
        raise ScriptureImportError(
            f"Importação cancelada por falha de integridade: {error}"
        ) from error

    return {
        "translation_code": translation["code"],
        "book_count": len(books),
        "chapter_count": chapter_count,
        "verse_count": verse_count,
        "note_count": note_count,
        "batch_id": batch_id,
    }


def import_translation_file(connection, file_path, approval):
    path = Path(file_path)
    try:
        raw_data = path.read_bytes()
        payload = json.loads(raw_data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ScriptureImportError(
            f"Não foi possível ler o arquivo JSON de importação: {path}"
        ) from error

    return import_translation(
        connection,
        payload,
        approval,
        hashlib.sha256(raw_data).hexdigest(),
    )
