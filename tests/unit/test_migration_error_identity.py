import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.migration_errors import PreflightError
from scripts.migrate_sqlite_to_postgres import _format_preflight_diagnostic
from scripts.migrate_sqlite_to_postgres import PreflightError as CliPreflightError
from scripts.sqlite_postgres_transfer import PreflightError as TransferPreflightError


ROOT = Path(__file__).resolve().parents[2]
CLI_SCRIPT = ROOT / "scripts" / "migrate_sqlite_to_postgres.py"


def test_imported_modules_share_the_single_preflight_error_class():
    assert CliPreflightError is PreflightError
    assert TransferPreflightError is PreflightError


def test_diagnostic_formatter_includes_only_explicit_safe_metadata():
    error = PreflightError(
        "internal message with secret text",
        diagnostic_code="SCHEMA_DEFAULT_MISMATCH",
        diagnostic_details={
            "table": "translations",
            "column": "license_status",
            "expected_default_present": True,
            "actual_default_present": False,
        },
    )

    formatted = _format_preflight_diagnostic(error)

    assert "SCHEMA_DEFAULT_MISMATCH" in formatted
    assert "translations" in formatted
    assert "license_status" in formatted
    assert "internal message" not in formatted
    assert "secret text" not in formatted


def test_direct_script_namespace_uses_shared_preflight_error_class():
    namespace = runpy.run_path(str(CLI_SCRIPT), run_name="direct_cli_test")

    assert namespace["PreflightError"] is PreflightError


@pytest.mark.parametrize(
    "command",
    [
        [sys.executable, str(CLI_SCRIPT), "--migrate-local-test"],
        [
            sys.executable,
            "-m",
            "scripts.migrate_sqlite_to_postgres",
            "--migrate-local-test",
        ],
    ],
    ids=["direct-script", "module"],
)
def test_cli_reports_safe_diagnostic_code_without_connecting(
    command,
):
    result = subprocess.run(
        command,
        cwd=ROOT,
        input="",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert "code=INTERACTIVE_TERMINAL_REQUIRED" in result.stderr
    assert "Detalhes omitidos" not in result.stderr
    assert "Senha" not in result.stderr
    assert result.stdout == ""


def test_connection_failure_reports_stage_code_without_error_details(
    monkeypatch, capsys
):
    import getpass
    import scripts.migrate_sqlite_to_postgres as cli

    class InteractiveInput:
        def isatty(self):
            return True

    terminal_input = InteractiveInput()
    monkeypatch.setattr(cli.sys, "stdin", terminal_input)
    monkeypatch.setattr(cli.sys, "__stdin__", terminal_input)
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "sensitive-password")

    def fail_connect(**_kwargs):
        raise RuntimeError("sensitive-password and private data")

    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        SimpleNamespace(connect=fail_connect),
    )

    assert cli.main(["--dry-run"]) == 2
    output = capsys.readouterr()

    assert "code=POSTGRES_CONNECTION_FAILED" in output.err
    assert "sensitive-password" not in output.err
    assert "private data" not in output.err
