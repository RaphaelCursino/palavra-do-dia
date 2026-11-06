CREATE TABLE favorites (
    id INTEGER PRIMARY KEY,
    owner_key TEXT NOT NULL DEFAULT 'local-installation',
    favorite_type TEXT NOT NULL CHECK (favorite_type IN ('verse', 'reflection')),
    translation_id INTEGER REFERENCES translations(id) ON DELETE RESTRICT,
    chapter_id INTEGER REFERENCES chapters(id) ON DELETE RESTRICT,
    verse_number INTEGER CHECK (verse_number > 0),
    verse_text TEXT,
    reference_text TEXT,
    reflection_id INTEGER REFERENCES reflections(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (length(trim(owner_key)) > 0),
    CHECK (
        (
            favorite_type = 'verse'
            AND translation_id IS NOT NULL
            AND chapter_id IS NOT NULL
            AND verse_number IS NOT NULL
            AND length(trim(verse_text)) > 0
            AND length(trim(reference_text)) > 0
            AND reflection_id IS NULL
        )
        OR
        (
            favorite_type = 'reflection'
            AND translation_id IS NULL
            AND chapter_id IS NULL
            AND verse_number IS NULL
            AND verse_text IS NULL
            AND reference_text IS NULL
            AND reflection_id IS NOT NULL
        )
    ),
    FOREIGN KEY (translation_id, chapter_id, verse_number)
        REFERENCES verses (translation_id, chapter_id, verse_number)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX idx_favorites_unique_verse
    ON favorites (owner_key, translation_id, chapter_id, verse_number)
    WHERE favorite_type = 'verse';
CREATE UNIQUE INDEX idx_favorites_unique_reflection
    ON favorites (owner_key, reflection_id)
    WHERE favorite_type = 'reflection';
CREATE INDEX idx_favorites_owner_created
    ON favorites (owner_key, created_at DESC, id DESC);
