CREATE TABLE translations (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    language_code TEXT NOT NULL,
    rights_holder TEXT NOT NULL,
    license_name TEXT NOT NULL,
    license_url TEXT NOT NULL,
    license_scope TEXT NOT NULL,
    attribution_text TEXT NOT NULL,
    license_reviewed_by TEXT NOT NULL,
    license_reviewed_at TEXT NOT NULL,
    license_evidence_url TEXT NOT NULL,
    license_status TEXT NOT NULL DEFAULT 'approved'
        CHECK (license_status = 'approved'),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (length(trim(code)) > 0),
    CHECK (length(trim(name)) > 0),
    CHECK (length(trim(language_code)) > 0),
    CHECK (length(trim(rights_holder)) > 0),
    CHECK (length(trim(license_name)) > 0),
    CHECK (length(trim(license_url)) > 0),
    CHECK (length(trim(license_scope)) > 0),
    CHECK (length(trim(attribution_text)) > 0),
    CHECK (length(trim(license_reviewed_by)) > 0),
    CHECK (length(trim(license_reviewed_at)) > 0),
    CHECK (length(trim(license_evidence_url)) > 0)
);

CREATE TABLE books (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name_pt TEXT NOT NULL,
    canonical_order INTEGER NOT NULL UNIQUE CHECK (canonical_order > 0),
    testament TEXT NOT NULL CHECK (testament IN ('OT', 'NT')),
    CHECK (length(trim(code)) > 0),
    CHECK (length(trim(name_pt)) > 0)
);

CREATE TABLE chapters (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    chapter_number INTEGER NOT NULL CHECK (chapter_number > 0),
    UNIQUE (book_id, chapter_number)
);

CREATE TABLE import_batches (
    id INTEGER PRIMARY KEY,
    translation_id INTEGER NOT NULL REFERENCES translations(id) ON DELETE RESTRICT,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    importer_version TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    book_count INTEGER NOT NULL CHECK (book_count > 0),
    chapter_count INTEGER NOT NULL CHECK (chapter_count > 0),
    verse_count INTEGER NOT NULL CHECK (verse_count > 0),
    CHECK (length(trim(source_name)) > 0),
    CHECK (length(trim(source_url)) > 0),
    CHECK (length(source_sha256) = 64),
    CHECK (length(trim(importer_version)) > 0)
);

CREATE TABLE verses (
    id INTEGER PRIMARY KEY,
    translation_id INTEGER NOT NULL REFERENCES translations(id) ON DELETE RESTRICT,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE RESTRICT,
    verse_number INTEGER NOT NULL CHECK (verse_number > 0),
    text TEXT NOT NULL CHECK (length(trim(text)) > 0),
    import_batch_id INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE RESTRICT,
    UNIQUE (translation_id, chapter_id, verse_number)
);

CREATE INDEX idx_chapters_book_number
    ON chapters (book_id, chapter_number);
CREATE INDEX idx_verses_translation_chapter_number
    ON verses (translation_id, chapter_id, verse_number);
CREATE INDEX idx_import_batches_translation
    ON import_batches (translation_id, imported_at);

INSERT INTO books (code, name_pt, canonical_order, testament) VALUES
    ('GEN', 'Gênesis', 1, 'OT'),
    ('EXO', 'Êxodo', 2, 'OT'),
    ('LEV', 'Levítico', 3, 'OT'),
    ('NUM', 'Números', 4, 'OT'),
    ('DEU', 'Deuteronômio', 5, 'OT'),
    ('JOS', 'Josué', 6, 'OT'),
    ('JDG', 'Juízes', 7, 'OT'),
    ('RUT', 'Rute', 8, 'OT'),
    ('1SA', '1 Samuel', 9, 'OT'),
    ('2SA', '2 Samuel', 10, 'OT'),
    ('1KI', '1 Reis', 11, 'OT'),
    ('2KI', '2 Reis', 12, 'OT'),
    ('1CH', '1 Crônicas', 13, 'OT'),
    ('2CH', '2 Crônicas', 14, 'OT'),
    ('EZR', 'Esdras', 15, 'OT'),
    ('NEH', 'Neemias', 16, 'OT'),
    ('EST', 'Ester', 17, 'OT'),
    ('JOB', 'Jó', 18, 'OT'),
    ('PSA', 'Salmos', 19, 'OT'),
    ('PRO', 'Provérbios', 20, 'OT'),
    ('ECC', 'Eclesiastes', 21, 'OT'),
    ('SNG', 'Cânticos', 22, 'OT'),
    ('ISA', 'Isaías', 23, 'OT'),
    ('JER', 'Jeremias', 24, 'OT'),
    ('LAM', 'Lamentações', 25, 'OT'),
    ('EZK', 'Ezequiel', 26, 'OT'),
    ('DAN', 'Daniel', 27, 'OT'),
    ('HOS', 'Oseias', 28, 'OT'),
    ('JOL', 'Joel', 29, 'OT'),
    ('AMO', 'Amós', 30, 'OT'),
    ('OBA', 'Obadias', 31, 'OT'),
    ('JON', 'Jonas', 32, 'OT'),
    ('MIC', 'Miqueias', 33, 'OT'),
    ('NAM', 'Naum', 34, 'OT'),
    ('HAB', 'Habacuque', 35, 'OT'),
    ('ZEP', 'Sofonias', 36, 'OT'),
    ('HAG', 'Ageu', 37, 'OT'),
    ('ZEC', 'Zacarias', 38, 'OT'),
    ('MAL', 'Malaquias', 39, 'OT'),
    ('MAT', 'Mateus', 40, 'NT'),
    ('MRK', 'Marcos', 41, 'NT'),
    ('LUK', 'Lucas', 42, 'NT'),
    ('JHN', 'João', 43, 'NT'),
    ('ACT', 'Atos', 44, 'NT'),
    ('ROM', 'Romanos', 45, 'NT'),
    ('1CO', '1 Coríntios', 46, 'NT'),
    ('2CO', '2 Coríntios', 47, 'NT'),
    ('GAL', 'Gálatas', 48, 'NT'),
    ('EPH', 'Efésios', 49, 'NT'),
    ('PHP', 'Filipenses', 50, 'NT'),
    ('COL', 'Colossenses', 51, 'NT'),
    ('1TH', '1 Tessalonicenses', 52, 'NT'),
    ('2TH', '2 Tessalonicenses', 53, 'NT'),
    ('1TI', '1 Timóteo', 54, 'NT'),
    ('2TI', '2 Timóteo', 55, 'NT'),
    ('TIT', 'Tito', 56, 'NT'),
    ('PHM', 'Filemom', 57, 'NT'),
    ('HEB', 'Hebreus', 58, 'NT'),
    ('JAS', 'Tiago', 59, 'NT'),
    ('1PE', '1 Pedro', 60, 'NT'),
    ('2PE', '2 Pedro', 61, 'NT'),
    ('1JN', '1 João', 62, 'NT'),
    ('2JN', '2 João', 63, 'NT'),
    ('3JN', '3 João', 64, 'NT'),
    ('JUD', 'Judas', 65, 'NT'),
    ('REV', 'Apocalipse', 66, 'NT');
