import re
import sqlite3
from pathlib import Path

import pytest

from app.services.canonical_books import (
    CANONICAL_BOOKS,
    CanonicalBooksSeedError,
    seed_canonical_books,
)


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "001_scripture_schema.sql"
)


@pytest.fixture
def books_connection():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name_pt TEXT NOT NULL,
            canonical_order INTEGER NOT NULL UNIQUE,
            testament TEXT NOT NULL CHECK (testament IN ('OT', 'NT'))
        )
        """
    )
    yield connection
    connection.close()


def _read_books(connection):
    return tuple(
        tuple(row)
        for row in connection.execute(
            """
            SELECT code, name_pt, canonical_order, testament
            FROM books
            ORDER BY canonical_order
            """
        ).fetchall()
    )


def test_catalog_matches_the_original_sqlite_migration():
    migration = MIGRATION_PATH.read_text(encoding="utf-8")
    original_rows = tuple(
        (
            code,
            name,
            int(canonical_order),
            testament,
        )
        for code, name, canonical_order, testament in re.findall(
            r"\('([^']+)', '([^']+)', (\d+), '(OT|NT)'\)",
            migration,
        )
    )

    assert len(original_rows) == 66
    assert CANONICAL_BOOKS == original_rows


def test_empty_database_is_seeded_with_all_canonical_books(books_connection):
    result = seed_canonical_books(books_connection)

    assert result == {"book_count": 66, "already_seeded": False}
    assert _read_books(books_connection) == CANONICAL_BOOKS


def test_repeated_seed_is_idempotent(books_connection):
    first_result = seed_canonical_books(books_connection)
    second_result = seed_canonical_books(books_connection)

    assert first_result["already_seeded"] is False
    assert second_result == {"book_count": 66, "already_seeded": True}
    assert len(_read_books(books_connection)) == 66


def test_divergent_existing_books_are_preserved_and_rejected(books_connection):
    books_connection.execute(
        """
        INSERT INTO books (code, name_pt, canonical_order, testament)
        VALUES (?, ?, ?, ?)
        """,
        ("GEN", "Nome divergente", 1, "OT"),
    )
    books_connection.commit()

    with pytest.raises(CanonicalBooksSeedError, match="divergentes"):
        seed_canonical_books(books_connection)

    assert _read_books(books_connection) == (
        ("GEN", "Nome divergente", 1, "OT"),
    )


def test_insert_failure_rolls_back_the_entire_seed(books_connection):
    books_connection.executescript(
        """
        CREATE TRIGGER reject_book_insert
        BEFORE INSERT ON books
        WHEN NEW.canonical_order = 10
        BEGIN
            SELECT RAISE(ABORT, 'simulated insert failure');
        END;
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="simulated insert failure"):
        seed_canonical_books(books_connection)

    assert _read_books(books_connection) == ()


def test_explicit_cli_command_requires_confirmation_and_is_repeatable(app):
    runner = app.test_cli_runner()

    unconfirmed = runner.invoke(args=["seed-canonical-books"])
    confirmed = runner.invoke(
        args=["seed-canonical-books", "--confirm"],
    )

    assert unconfirmed.exit_code != 0
    assert "bloqueada" in unconfirmed.output
    assert confirmed.exit_code == 0
    assert "66 livros" in confirmed.output
