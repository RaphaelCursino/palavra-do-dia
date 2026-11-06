CREATE TABLE verse_notes (
    id INTEGER PRIMARY KEY,
    verse_id INTEGER NOT NULL REFERENCES verses(id) ON DELETE RESTRICT,
    note_type TEXT NOT NULL CHECK (note_type IN ('footnote', 'cross_reference')),
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    note_text TEXT NOT NULL CHECK (length(trim(note_text)) > 0),
    UNIQUE (verse_id, note_type, ordinal)
);

CREATE INDEX idx_verse_notes_verse
    ON verse_notes (verse_id, note_type, ordinal);
