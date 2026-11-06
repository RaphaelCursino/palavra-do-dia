CANONICAL_BOOKS = (
    ("GEN", "Gênesis", 1, "OT"),
    ("EXO", "Êxodo", 2, "OT"),
    ("LEV", "Levítico", 3, "OT"),
    ("NUM", "Números", 4, "OT"),
    ("DEU", "Deuteronômio", 5, "OT"),
    ("JOS", "Josué", 6, "OT"),
    ("JDG", "Juízes", 7, "OT"),
    ("RUT", "Rute", 8, "OT"),
    ("1SA", "1 Samuel", 9, "OT"),
    ("2SA", "2 Samuel", 10, "OT"),
    ("1KI", "1 Reis", 11, "OT"),
    ("2KI", "2 Reis", 12, "OT"),
    ("1CH", "1 Crônicas", 13, "OT"),
    ("2CH", "2 Crônicas", 14, "OT"),
    ("EZR", "Esdras", 15, "OT"),
    ("NEH", "Neemias", 16, "OT"),
    ("EST", "Ester", 17, "OT"),
    ("JOB", "Jó", 18, "OT"),
    ("PSA", "Salmos", 19, "OT"),
    ("PRO", "Provérbios", 20, "OT"),
    ("ECC", "Eclesiastes", 21, "OT"),
    ("SNG", "Cânticos", 22, "OT"),
    ("ISA", "Isaías", 23, "OT"),
    ("JER", "Jeremias", 24, "OT"),
    ("LAM", "Lamentações", 25, "OT"),
    ("EZK", "Ezequiel", 26, "OT"),
    ("DAN", "Daniel", 27, "OT"),
    ("HOS", "Oseias", 28, "OT"),
    ("JOL", "Joel", 29, "OT"),
    ("AMO", "Amós", 30, "OT"),
    ("OBA", "Obadias", 31, "OT"),
    ("JON", "Jonas", 32, "OT"),
    ("MIC", "Miqueias", 33, "OT"),
    ("NAM", "Naum", 34, "OT"),
    ("HAB", "Habacuque", 35, "OT"),
    ("ZEP", "Sofonias", 36, "OT"),
    ("HAG", "Ageu", 37, "OT"),
    ("ZEC", "Zacarias", 38, "OT"),
    ("MAL", "Malaquias", 39, "OT"),
    ("MAT", "Mateus", 40, "NT"),
    ("MRK", "Marcos", 41, "NT"),
    ("LUK", "Lucas", 42, "NT"),
    ("JHN", "João", 43, "NT"),
    ("ACT", "Atos", 44, "NT"),
    ("ROM", "Romanos", 45, "NT"),
    ("1CO", "1 Coríntios", 46, "NT"),
    ("2CO", "2 Coríntios", 47, "NT"),
    ("GAL", "Gálatas", 48, "NT"),
    ("EPH", "Efésios", 49, "NT"),
    ("PHP", "Filipenses", 50, "NT"),
    ("COL", "Colossenses", 51, "NT"),
    ("1TH", "1 Tessalonicenses", 52, "NT"),
    ("2TH", "2 Tessalonicenses", 53, "NT"),
    ("1TI", "1 Timóteo", 54, "NT"),
    ("2TI", "2 Timóteo", 55, "NT"),
    ("TIT", "Tito", 56, "NT"),
    ("PHM", "Filemom", 57, "NT"),
    ("HEB", "Hebreus", 58, "NT"),
    ("JAS", "Tiago", 59, "NT"),
    ("1PE", "1 Pedro", 60, "NT"),
    ("2PE", "2 Pedro", 61, "NT"),
    ("1JN", "1 João", 62, "NT"),
    ("2JN", "2 João", 63, "NT"),
    ("3JN", "3 João", 64, "NT"),
    ("JUD", "Judas", 65, "NT"),
    ("REV", "Apocalipse", 66, "NT"),
)


class CanonicalBooksSeedError(ValueError):
    pass


def _read_books(connection):
    rows = connection.execute(
        """
        SELECT code, name_pt, canonical_order, testament
        FROM books
        ORDER BY canonical_order
        """
    ).fetchall()
    return tuple(
        (
            row["code"],
            row["name_pt"],
            row["canonical_order"],
            row["testament"],
        )
        for row in rows
    )


def seed_canonical_books(connection):
    try:
        existing_books = _read_books(connection)
        if existing_books and existing_books != CANONICAL_BOOKS:
            raise CanonicalBooksSeedError(
                "A tabela books já contém registros ausentes ou divergentes "
                "do catálogo canônico; nenhum dado foi alterado."
            )

        if not existing_books:
            connection.executemany(
                """
                INSERT INTO books (code, name_pt, canonical_order, testament)
                VALUES (?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                CANONICAL_BOOKS,
            )

        if _read_books(connection) != CANONICAL_BOOKS:
            raise CanonicalBooksSeedError(
                "A carga não produziu exatamente os 66 livros canônicos; "
                "a transação foi revertida."
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    return {
        "book_count": len(CANONICAL_BOOKS),
        "already_seeded": bool(existing_books),
    }
