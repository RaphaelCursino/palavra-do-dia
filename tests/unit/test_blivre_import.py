import hashlib
import shutil
from pathlib import Path

import pytest

from app.db import get_db
from app.repositories.scripture import get_verse, list_verse_notes
from app.services.blivre_import import (
    BLIVRE_ATTRIBUTION,
    BLIVRE_CODE,
    EXPECTED_ARCHIVE_SHA256,
    import_blivre_archive,
    parse_blivre_usfm_archive,
)
from app.services.scripture_import import LicenseApproval, ScriptureImportError


ARCHIVE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "imports"
    / "blivre"
    / "porbr2018_usfm.zip"
)


def blivre_approval():
    return LicenseApproval(
        reviewed_by="Solicitação do usuário",
        reviewed_at="2026-11-05",
        evidence_url="https://ebible.org/find/details.php?id=porbr2018",
        confirmed=True,
    )


def test_official_usfm_archive_has_complete_canon_and_preserves_notes():
    payload, archive_sha256 = parse_blivre_usfm_archive(
        ARCHIVE_PATH,
        "2026-11-05",
    )

    books = payload["books"]
    chapters = [chapter for book in books for chapter in book["chapters"]]
    verses = [verse for chapter in chapters for verse in chapter["verses"]]
    notes = [note for verse in verses for note in verse["notes"]]
    genesis_1_1 = books[0]["chapters"][0]["verses"][0]
    genesis_3_5 = books[0]["chapters"][2]["verses"][4]

    assert archive_sha256 == EXPECTED_ARCHIVE_SHA256
    assert [book["code"] for book in books][0:3] == ["GEN", "EXO", "LEV"]
    assert [book["code"] for book in books][-3:] == ["3JN", "JUD", "REV"]
    assert len(books) == 66
    assert len(chapters) == 1189
    assert len(verses) == 31102
    assert len(notes) == 1290
    assert genesis_1_1["text"] == "No princípio criou Deus os céus e a terra."
    assert genesis_3_5["text"].endswith("sabendo o bem e o mal.")
    assert genesis_3_5["notes"] == [
        {"type": "footnote", "text": "3:5 Ou: sereis como Deus"}
    ]
    assert payload["translation"]["code"] == "blivre"
    assert payload["translation"]["name"] == "Bíblia Livre (BLIVRE)"
    assert payload["translation"]["license"]["attribution"] == BLIVRE_ATTRIBUTION


def test_official_import_is_complete_credited_and_idempotent(app):
    with app.app_context():
        connection = get_db()
        result = import_blivre_archive(
            connection,
            ARCHIVE_PATH,
            blivre_approval(),
            "2026-11-05",
        )
        repeated = import_blivre_archive(
            connection,
            ARCHIVE_PATH,
            blivre_approval(),
            "2026-11-05",
        )
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("translations", "chapters", "verses", "verse_notes")
        }
        books = connection.execute(
            """
            SELECT COUNT(DISTINCT books.id)
            FROM translations
            JOIN verses ON verses.translation_id = translations.id
            JOIN chapters ON chapters.id = verses.chapter_id
            JOIN books ON books.id = chapters.book_id
            WHERE translations.code = ?
            """,
            (BLIVRE_CODE,),
        ).fetchone()[0]
        translation = connection.execute(
            """
            SELECT name, rights_holder, license_name, license_url,
                   attribution_text, license_reviewed_by, license_evidence_url
            FROM translations WHERE code = ?
            """,
            (BLIVRE_CODE,),
        ).fetchone()
        batch = connection.execute(
            """
            SELECT source_url, source_sha256, importer_version, book_count,
                   chapter_count, verse_count
            FROM import_batches
            WHERE translation_id = (
                SELECT id FROM translations WHERE code = ?
            )
            """,
            (BLIVRE_CODE,),
        ).fetchone()
        first_verse = get_verse(connection, BLIVRE_CODE, "GEN", 1, 1)
        first_verse_notes = list_verse_notes(
            connection,
            [first_verse["verse_id"]],
        )
        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

    assert result["already_imported"] is False
    assert result["book_count"] == 66
    assert result["chapter_count"] == 1189
    assert result["verse_count"] == 31102
    assert result["note_count"] == 1290
    assert result["source_sha256"] == EXPECTED_ARCHIVE_SHA256
    assert repeated["already_imported"] is True
    assert repeated["batch_id"] == result["batch_id"]
    assert counts == {
        "translations": 1,
        "chapters": 1189,
        "verses": 31102,
        "verse_notes": 1290,
    }
    assert books == 66
    assert translation["name"] == "Bíblia Livre (BLIVRE)"
    assert translation["rights_holder"] == "Diego Santos, Mario Sérgio e Marco Teles"
    assert translation["license_name"] == (
        "Creative Commons Atribuição 4.0 Brasil (CC BY 4.0)"
    )
    assert translation["license_url"] == "https://creativecommons.org/licenses/by/4.0/br/"
    assert translation["attribution_text"] == BLIVRE_ATTRIBUTION
    assert translation["license_reviewed_by"] == "Solicitação do usuário"
    assert translation["license_evidence_url"] == (
        "https://ebible.org/find/details.php?id=porbr2018"
    )
    assert batch["source_url"] == "https://ebible.org/Scriptures/porbr2018_usfm.zip"
    assert batch["source_sha256"] == EXPECTED_ARCHIVE_SHA256
    assert batch["importer_version"] == "blivre-usfm-1"
    assert tuple(batch[key] for key in ("book_count", "chapter_count", "verse_count")) == (
        66,
        1189,
        31102,
    )
    assert first_verse["text"] == "No princípio criou Deus os céus e a terra."
    assert first_verse_notes == []
    assert foreign_key_violations == []


def test_modified_archive_hash_is_blocked_without_database_changes(app, tmp_path):
    modified_archive = tmp_path / "modified-usfm.zip"
    shutil.copyfile(ARCHIVE_PATH, modified_archive)
    with modified_archive.open("ab") as archive_file:
        archive_file.write(b"altered source")

    with app.app_context():
        connection = get_db()
        with pytest.raises(ScriptureImportError, match="Hash do pacote BLIVRE"):
            import_blivre_archive(
                connection,
                modified_archive,
                blivre_approval(),
                "2026-11-05",
            )
        assert connection.execute("SELECT COUNT(*) FROM translations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM verses").fetchone()[0] == 0


def test_import_command_requires_explicit_license_confirmation(app):
    result = app.test_cli_runner().invoke(
        args=[
            "import-blivre",
            "--reviewed-by",
            "Responsável de teste",
            "--reviewed-at",
            "2026-11-05",
            "--acquired-at",
            "2026-11-05",
        ]
    )

    assert result.exit_code != 0
    assert "Importação bloqueada" in result.output
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM translations"
        ).fetchone()[0] == 0


def test_import_command_stops_when_canonical_archive_is_missing(
    app, tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "root_path", str(tmp_path / "app"))
    result = app.test_cli_runner().invoke(
        args=[
            "import-blivre",
            "--confirm-license",
            "--reviewed-by",
            "Responsável de teste",
            "--reviewed-at",
            "2026-11-05",
            "--acquired-at",
            "2026-11-05",
        ]
    )

    assert result.exit_code != 0
    assert "data/imports/blivre/porbr2018_usfm.zip não encontrado" in result.output
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM translations"
        ).fetchone()[0] == 0


def test_import_command_imports_canonical_archive_and_is_idempotent(app):
    arguments = [
        "import-blivre",
        "--confirm-license",
        "--reviewed-by",
        "Responsável de teste",
        "--reviewed-at",
        "2026-11-05",
        "--acquired-at",
        "2026-11-05",
    ]

    first = app.test_cli_runner().invoke(args=arguments)
    second = app.test_cli_runner().invoke(args=arguments)

    assert first.exit_code == 0, first.output
    assert "BLIVRE importada: 66 livro(s), 1189 capítulo(s), 31102 versículo(s)" in first.output
    assert second.exit_code == 0, second.output
    assert "BLIVRE já estava importada" in second.output
    with app.app_context():
        connection = get_db()
        assert connection.execute(
            "SELECT COUNT(*) FROM translations WHERE code = 'blivre'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM verses"
        ).fetchone()[0] == 31102
        assert connection.execute(
            "SELECT COUNT(*) FROM verse_notes"
        ).fetchone()[0] == 1290
