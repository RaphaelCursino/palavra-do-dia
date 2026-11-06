import hashlib
import json

import pytest

from app.db import get_db
from app.repositories.scripture import (
    get_verse,
    list_books,
    list_chapter_verses,
)
from app.services.scripture_import import (
    LicenseApproval,
    ScriptureImportError,
    _validate_payload,
    import_translation,
)


def test_import_requires_explicit_license_confirmation(
    app, demo_payload, license_approval
):
    unconfirmed = LicenseApproval(
        reviewed_by=license_approval.reviewed_by,
        reviewed_at=license_approval.reviewed_at,
        evidence_url=license_approval.evidence_url,
        confirmed=False,
    )

    with app.app_context(), pytest.raises(ScriptureImportError, match="Importação bloqueada"):
        import_translation(
            get_db(),
            demo_payload,
            unconfirmed,
            hashlib.sha256(b"test only").hexdigest(),
        )

    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM translations").fetchone()[0] == 0


def test_import_registers_provenance_and_supports_scripture_queries(
    app, demo_payload, license_approval
):
    source_bytes = json.dumps(demo_payload, ensure_ascii=False).encode("utf-8")
    expected_hash = hashlib.sha256(source_bytes).hexdigest()

    with app.app_context():
        connection = get_db()
        result = import_translation(
            connection,
            demo_payload,
            license_approval,
            expected_hash,
        )

        assert result == {
            "translation_code": "demo-test",
            "book_count": 1,
            "chapter_count": 1,
            "verse_count": 2,
            "note_count": 0,
            "batch_id": 1,
        }
        books = list_books(connection, "demo-test")
        chapter = list_chapter_verses(connection, "demo-test", "GEN", 1)
        verse = get_verse(connection, "demo-test", "GEN", 1, 2)
        missing = get_verse(connection, "demo-test", "GEN", 1, 3)
        translation = connection.execute(
            """
            SELECT license_name, license_url, license_scope, attribution_text,
                   license_reviewed_by, license_reviewed_at,
                   license_evidence_url, license_status
            FROM translations WHERE code = ?
            """,
            ("demo-test",),
        ).fetchone()
        batch = connection.execute(
            """
            SELECT source_name, source_url, acquired_at, source_sha256,
                   book_count, chapter_count, verse_count
            FROM import_batches WHERE id = ?
            """,
            (result["batch_id"],),
        ).fetchone()
        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

    assert books[0]["code"] == "GEN"
    assert books[0]["name_pt"] == "Gênesis"
    assert [row["verse_number"] for row in chapter] == [1, 2]
    assert "NÃO É BÍBLIA" in verse["text"]
    assert missing is None
    assert translation["license_status"] == "approved"
    assert translation["license_reviewed_by"] == "Responsável dos testes"
    assert translation["license_url"] == "https://example.invalid/license"
    assert translation["attribution_text"] == "Conteúdo de teste não bíblico."
    assert batch["source_sha256"] == expected_hash
    assert batch["verse_count"] == 2
    assert foreign_key_violations == []


def test_invalid_book_rolls_back_the_whole_import(
    app, demo_payload, license_approval
):
    demo_payload["books"].append(
        {
            "code": "ZZZ",
            "chapters": [
                {"number": 1, "verses": [{"number": 1, "text": "DADO DE TESTE."}]}
            ],
        }
    )

    with app.app_context():
        connection = get_db()
        with pytest.raises(ScriptureImportError, match="Livro não pertence"):
            import_translation(
                connection,
                demo_payload,
                license_approval,
                hashlib.sha256(b"test only").hexdigest(),
            )

        assert connection.execute("SELECT COUNT(*) FROM translations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM chapters").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM verses").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data["books"][0]["chapters"][0]["verses"].append(
            {"number": 1, "text": "DUPLICADO - NÃO É BÍBLIA."}
        ), "Versículo duplicado"),
        (lambda data: data["books"][0]["chapters"][0]["verses"][0].update(
            text="   "
        ), "Campo obrigatório ausente ou inválido"),
        (lambda data: data["source"].update(url="http://example.invalid/source"), "HTTPS"),
        (lambda data: data["source"].update(acquired_at="20260102"), "AAAA-MM-DD"),
    ],
)
def test_import_rejects_malformed_content(demo_payload, mutate, message):
    mutate(demo_payload)
    with pytest.raises(ScriptureImportError, match=message):
        _validate_payload(
            demo_payload,
            LicenseApproval(
                reviewed_by="Responsável dos testes",
                reviewed_at="2026-01-03",
                evidence_url="https://example.invalid/test-evidence",
                confirmed=True,
            ),
        )


def test_duplicate_translation_import_is_rejected_without_overwrite(
    app, demo_payload, license_approval
):
    digest = hashlib.sha256(b"test only").hexdigest()

    with app.app_context():
        connection = get_db()
        import_translation(connection, demo_payload, license_approval, digest)
        with pytest.raises(ScriptureImportError, match="já existe"):
            import_translation(connection, demo_payload, license_approval, digest)

        assert connection.execute("SELECT COUNT(*) FROM translations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM verses").fetchone()[0] == 2
