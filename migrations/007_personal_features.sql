CREATE TABLE verse_annotations (
    id INTEGER PRIMARY KEY,
    owner_key TEXT NOT NULL DEFAULT 'local-installation',
    verse_id INTEGER NOT NULL REFERENCES verses(id) ON DELETE RESTRICT,
    note_text TEXT NOT NULL CHECK (length(trim(note_text)) BETWEEN 1 AND 5000),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (length(trim(owner_key)) > 0),
    UNIQUE (owner_key, verse_id)
);

CREATE INDEX idx_verse_annotations_owner_updated
    ON verse_annotations (owner_key, updated_at DESC, id DESC);

CREATE TABLE verse_highlights (
    favorite_id INTEGER PRIMARY KEY
        REFERENCES favorites(id) ON DELETE CASCADE,
    color TEXT NOT NULL
        CHECK (color IN ('gold', 'blue', 'green', 'rose', 'lavender')),
    category TEXT NOT NULL
        CHECK (category IN ('faith', 'hope', 'love', 'gratitude', 'prayer', 'reflection')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_verse_highlights_category_color
    ON verse_highlights (category, color);

CREATE TABLE prayer_entries (
    id INTEGER PRIMARY KEY,
    owner_key TEXT NOT NULL DEFAULT 'local-installation',
    entry_type TEXT NOT NULL CHECK (entry_type IN ('request', 'gratitude')),
    title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 120),
    description TEXT NOT NULL CHECK (length(trim(description)) BETWEEN 1 AND 5000),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'answered')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    answered_at TEXT,
    CHECK (length(trim(owner_key)) > 0),
    CHECK (
        (status = 'active' AND answered_at IS NULL)
        OR (status = 'answered' AND entry_type = 'request' AND answered_at IS NOT NULL)
    )
);

CREATE INDEX idx_prayer_entries_owner_type_status_created
    ON prayer_entries (owner_key, entry_type, status, created_at DESC, id DESC);

CREATE TABLE reader_preferences (
    owner_key TEXT PRIMARY KEY DEFAULT 'local-installation',
    theme TEXT NOT NULL DEFAULT 'light' CHECK (theme IN ('light', 'dark')),
    font_scale INTEGER NOT NULL DEFAULT 100 CHECK (font_scale BETWEEN 80 AND 150),
    verse_spacing TEXT NOT NULL DEFAULT 'comfortable'
        CHECK (verse_spacing IN ('comfortable', 'compact')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (length(trim(owner_key)) > 0)
);
