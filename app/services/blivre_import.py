import hashlib
import re
import zipfile
from datetime import date
from pathlib import Path

from .scripture_import import (
    LicenseApproval,
    ScriptureImportError,
    import_translation,
)


BLIVRE_CODE = "blivre"
BLIVRE_NAME = "Bíblia Livre (BLIVRE)"
BLIVRE_SOURCE_PAGE = "https://ebible.org/find/details.php?id=porbr2018"
BLIVRE_ARCHIVE_URL = "https://ebible.org/Scriptures/porbr2018_usfm.zip"
BLIVRE_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/br/"
BLIVRE_RIGHTS_HOLDER = "Diego Santos, Mario Sérgio e Marco Teles"
BLIVRE_ATTRIBUTION = (
    "Todas as Escrituras em português são da Bíblia Livre (BLIVRE), Copyright "
    "© 2018 Diego Santos, Mario Sérgio e Marco Teles. Fonte: "
    "https://sites.google.com/site/biblialivre/. Licença Creative Commons "
    "Atribuição 4.0 Brasil: "
    "https://creativecommons.org/licenses/by/4.0/br/."
)
BLIVRE_SCOPE = (
    "Texto integral da Bíblia Livre (edição porbr2018), disponibilizado pelo "
    "eBible.org sob Creative Commons Atribuição 4.0 Brasil (CC BY 4.0); "
    "redistribuição permitida com atribuição. O arquivo original foi mantido "
    "sem alterações; marcadores estruturais USFM são convertidos para o "
    "esquema do aplicativo e notas são armazenadas separadamente."
)
BLIVRE_IMPORTER_VERSION = "blivre-usfm-1"
MAX_ARCHIVE_SIZE = 10 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE = 20 * 1024 * 1024
BOOK_FILE = re.compile(r"^\d{2}-(?P<code>[A-Z0-9]{3})porbr2018\.usfm$")
BOOK_ID = re.compile(r"^\\id\s+([A-Z0-9]{3})\b", re.MULTILINE)
CHAPTER_MARKER = re.compile(r"^\\c\s+([0-9]+)\b")
VERSE_MARKER = re.compile(r"\\v\s+([0-9]+)\b")
FOOTNOTE = re.compile(r"\\f\s+(.+?)\\f\*", re.DOTALL)
CROSS_REFERENCE = re.compile(r"\\x\s+(.+?)\\x\*", re.DOTALL)
USFM_MARKER = re.compile(r"\\[A-Za-z0-9+]+\*?")
EXPECTED_ARCHIVE_SHA256 = (
    "7ca6742fc1a32ca657a9872047c919097bb9af1384dc318c9cc58ab1cfa5bbf9"
)


def parse_blivre_usfm_archive(archive_path, acquired_at):
    try:
        date.fromisoformat(acquired_at)
    except (TypeError, ValueError) as error:
        raise ScriptureImportError("Data de aquisição inválida (use AAAA-MM-DD).") from error

    path = Path(archive_path)
    try:
        archive_bytes = path.read_bytes()
    except OSError as error:
        raise ScriptureImportError(
            f"Não foi possível ler o arquivo USFM: {path}"
        ) from error
    if not archive_bytes or len(archive_bytes) > MAX_ARCHIVE_SIZE:
        raise ScriptureImportError("Arquivo USFM ausente ou acima do limite permitido.")

    try:
        with zipfile.ZipFile(path) as archive:
            members = [
                member
                for member in archive.infolist()
                if not member.is_dir() and member.filename.lower().endswith(".usfm")
            ]
            if (
                sum(member.file_size for member in members) > MAX_UNCOMPRESSED_SIZE
                or any(member.file_size > MAX_UNCOMPRESSED_SIZE for member in members)
            ):
                raise ScriptureImportError(
                    "O pacote USFM excede o limite de conteúdo descompactado."
                )
            parsed_books = {}
            for member in members:
                match = BOOK_FILE.fullmatch(Path(member.filename).name)
                if match is None:
                    raise ScriptureImportError(
                        f"Arquivo USFM não reconhecido no pacote: {member.filename}"
                    )
                expected_code = match.group("code")
                if expected_code in parsed_books:
                    raise ScriptureImportError(
                        f"Livro duplicado no pacote USFM: {expected_code}."
                    )
                try:
                    content = archive.read(member).decode("utf-8-sig")
                except (UnicodeDecodeError, OSError, zipfile.BadZipFile) as error:
                    raise ScriptureImportError(
                        f"Arquivo USFM inválido ou ilegível: {member.filename}"
                    ) from error
                parsed_books[expected_code] = _parse_book(content, expected_code)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ScriptureImportError("Pacote USFM inválido ou corrompido.") from error

    if set(parsed_books) != set(_CANONICAL_BOOK_CODES):
        missing = sorted(set(_CANONICAL_BOOK_CODES) - set(parsed_books))
        unexpected = sorted(set(parsed_books) - set(_CANONICAL_BOOK_CODES))
        raise ScriptureImportError(
            "O pacote USFM deve conter exatamente os 66 livros canônicos; "
            f"faltantes={missing}, inesperados={unexpected}."
        )

    ordered_books = [
        {"code": code, "chapters": parsed_books[code]}
        for code in _CANONICAL_BOOK_CODES
    ]
    payload = {
        "format_version": 1,
        "translation": {
            "code": BLIVRE_CODE,
            "name": BLIVRE_NAME,
            "language_code": "pt-BR",
            "rights_holder": BLIVRE_RIGHTS_HOLDER,
            "license": {
                "name": "Creative Commons Atribuição 4.0 Brasil (CC BY 4.0)",
                "url": BLIVRE_LICENSE_URL,
                "scope": BLIVRE_SCOPE,
                "attribution": BLIVRE_ATTRIBUTION,
            },
        },
        "source": {
            "name": "eBible.org — Bíblia Livre porbr2018, pacote USFM",
            "url": BLIVRE_ARCHIVE_URL,
            "acquired_at": acquired_at,
        },
        "books": ordered_books,
    }
    return payload, hashlib.sha256(archive_bytes).hexdigest()


def import_blivre_archive(connection, archive_path, approval, acquired_at):
    payload, archive_sha256 = parse_blivre_usfm_archive(
        archive_path,
        acquired_at,
    )
    if archive_sha256 != EXPECTED_ARCHIVE_SHA256:
        raise ScriptureImportError(
            "Hash do pacote BLIVRE diferente da versão oficial verificada; "
            "a importação foi bloqueada."
        )
    existing = connection.execute(
        """
        SELECT translations.id AS translation_id,
               import_batches.id AS batch_id,
               import_batches.source_sha256,
               import_batches.book_count,
               import_batches.chapter_count,
               import_batches.verse_count
        FROM translations
        JOIN import_batches ON import_batches.translation_id = translations.id
        WHERE translations.code = ?
        ORDER BY import_batches.id
        """,
        (BLIVRE_CODE,),
    ).fetchall()
    if existing:
        if len(existing) == 1 and existing[0]["source_sha256"] == archive_sha256:
            result = {
                "translation_code": BLIVRE_CODE,
                "book_count": existing[0]["book_count"],
                "chapter_count": existing[0]["chapter_count"],
                "verse_count": existing[0]["verse_count"],
                "batch_id": existing[0]["batch_id"],
                "note_count": _count_notes_for_translation(
                    connection,
                    existing[0]["translation_id"],
                ),
                "source_sha256": archive_sha256,
                "already_imported": True,
            }
            return result
        raise ScriptureImportError(
            "BLIVRE já existe com outra origem, hash ou lote; nenhum dado "
            "foi sobrescrito."
        )

    result = import_translation(
        connection,
        payload,
        approval,
        archive_sha256,
        importer_version=BLIVRE_IMPORTER_VERSION,
    )
    result["source_sha256"] = archive_sha256
    result["already_imported"] = False
    return result


def _count_notes_for_translation(connection, translation_id):
    return connection.execute(
        """
        SELECT COUNT(*)
        FROM verse_notes
        JOIN verses ON verses.id = verse_notes.verse_id
        WHERE verses.translation_id = ?
        """,
        (translation_id,),
    ).fetchone()[0]


def _parse_book(content, expected_code):
    book_id = BOOK_ID.search(content)
    if book_id is None or book_id.group(1) != expected_code:
        raise ScriptureImportError(
            f"Código \\id ausente ou incompatível no livro {expected_code}."
        )

    chapters = []
    current_chapter = None
    current_verses = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        chapter_match = CHAPTER_MARKER.match(line)
        if chapter_match:
            if current_chapter is not None:
                _finish_chapter(
                    chapters,
                    current_chapter,
                    current_verses,
                    expected_code,
                )
            current_chapter = int(chapter_match.group(1))
            if current_chapter != len(chapters) + 1:
                raise ScriptureImportError(
                    f"Capítulo fora de ordem em {expected_code}:{line_number}."
                )
            current_verses = []
            continue

        verse_match = VERSE_MARKER.search(line)
        if verse_match:
            if current_chapter is None:
                raise ScriptureImportError(
                    f"Versículo antes do capítulo em {expected_code}:{line_number}."
                )
            verse_number = int(verse_match.group(1))
            body = line[verse_match.end():]
            verse_text, notes = _parse_inline_content(body)
            if not verse_text:
                raise ScriptureImportError(
                    f"Texto vazio em {expected_code} "
                    f"{current_chapter}:{verse_number}."
                )
            current_verses.append(
                {"number": verse_number, "text": verse_text, "notes": notes}
            )

    if current_chapter is not None:
        _finish_chapter(
            chapters,
            current_chapter,
            current_verses,
            expected_code,
        )
    if not chapters:
        raise ScriptureImportError(f"Nenhum capítulo encontrado em {expected_code}.")
    return chapters


def _finish_chapter(chapters, chapter_number, verses, book_code):
    if not verses:
        raise ScriptureImportError(
            f"Capítulo sem versículos em {book_code} {chapter_number}."
        )
    verse_numbers = [verse["number"] for verse in verses]
    if len(verse_numbers) != len(set(verse_numbers)):
        raise ScriptureImportError(
            f"Versículos duplicados em {book_code} {chapter_number}."
        )
    if verse_numbers != sorted(verse_numbers):
        raise ScriptureImportError(
            f"Versículos fora de ordem em {book_code} {chapter_number}."
        )
    chapters.append({"number": chapter_number, "verses": verses})


def _parse_inline_content(body):
    notes = []

    def extract_notes(pattern, note_type):
        def replace(match):
            note_text = _plain_usfm_text(match.group(1)).lstrip("+").strip()
            if not note_text:
                raise ScriptureImportError("Nota USFM vazia.")
            notes.append({"type": note_type, "text": note_text})
            return " "

        return pattern.sub(replace, body)

    text_without_notes = extract_notes(FOOTNOTE, "footnote")
    text_without_notes = CROSS_REFERENCE.sub(
        lambda match: _append_cross_reference(notes, match.group(1)),
        text_without_notes,
    )
    verse_text = _plain_usfm_text(text_without_notes)
    return verse_text, notes


def _append_cross_reference(notes, content):
    note_text = _plain_usfm_text(content).lstrip("+").strip()
    if not note_text:
        raise ScriptureImportError("Referência cruzada USFM vazia.")
    notes.append({"type": "cross_reference", "text": note_text})
    return " "


def _plain_usfm_text(content):
    content = re.sub(
        r"\\w\s+([^|\\]+)\|[^\\]*?\\w\*",
        r"\1",
        content,
    )
    content = re.sub(r"\\[A-Za-z0-9+]+\s", " ", content)
    content = USFM_MARKER.sub(" ", content)
    content = content.replace("~", " ").replace("//", " ")
    content = re.sub(r"\s+", " ", content)
    return content.strip()


_CANONICAL_BOOK_CODES = (
    "GEN", "EXO", "LEV", "NUM", "DEU", "JOS", "JDG", "RUT",
    "1SA", "2SA", "1KI", "2KI", "1CH", "2CH", "EZR", "NEH",
    "EST", "JOB", "PSA", "PRO", "ECC", "SNG", "ISA", "JER",
    "LAM", "EZK", "DAN", "HOS", "JOL", "AMO", "OBA", "JON",
    "MIC", "NAM", "HAB", "ZEP", "HAG", "ZEC", "MAL", "MAT",
    "MRK", "LUK", "JHN", "ACT", "ROM", "1CO", "2CO", "GAL",
    "EPH", "PHP", "COL", "1TH", "2TH", "1TI", "2TI", "TIT",
    "PHM", "HEB", "JAS", "1PE", "2PE", "1JN", "2JN", "3JN",
    "JUD", "REV",
)
