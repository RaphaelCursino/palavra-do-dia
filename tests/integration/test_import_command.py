import json
import hashlib

from app.db import get_db


def test_import_command_refuses_to_run_without_license_approval(app, demo_payload, tmp_path):
    source_file = tmp_path / "demo-import.json"
    source_file.write_text(json.dumps(demo_payload), encoding="utf-8")
    runner = app.test_cli_runner()

    result = runner.invoke(args=["import-scripture", str(source_file)])

    assert result.exit_code != 0
    assert "Importação bloqueada" in result.output
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM translations").fetchone()[0] == 0


def test_import_command_imports_test_fixture_after_explicit_confirmation(
    app, demo_payload, tmp_path
):
    source_file = tmp_path / "demo-import.json"
    source_file.write_text(json.dumps(demo_payload, ensure_ascii=False), encoding="utf-8")
    runner = app.test_cli_runner()

    result = runner.invoke(
        args=[
            "import-scripture",
            str(source_file),
            "--confirm-license",
            "--reviewed-by",
            "Responsável dos testes",
            "--reviewed-at",
            "2026-01-03",
            "--evidence-url",
            "https://example.invalid/test-evidence",
        ]
    )

    assert result.exit_code == 0, result.output
    assert "Importação concluída" in result.output
    with app.app_context():
        connection = get_db()
        assert connection.execute(
            "SELECT code FROM translations"
        ).fetchone()["code"] == "demo-test"
        batch = connection.execute(
            "SELECT source_sha256 FROM import_batches"
        ).fetchone()
        assert batch["source_sha256"] == hashlib.sha256(
            source_file.read_bytes()
        ).hexdigest()
