CREATE TABLE daily_readings (
    id INTEGER PRIMARY KEY,
    reading_date TEXT NOT NULL
        CHECK (reading_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    translation_id INTEGER NOT NULL REFERENCES translations(id) ON DELETE RESTRICT,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE RESTRICT,
    start_verse_number INTEGER NOT NULL CHECK (start_verse_number > 0),
    end_verse_number INTEGER NOT NULL CHECK (end_verse_number >= start_verse_number),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (reading_date, translation_id),
    FOREIGN KEY (translation_id, chapter_id, start_verse_number)
        REFERENCES verses (translation_id, chapter_id, verse_number)
        ON DELETE RESTRICT,
    FOREIGN KEY (translation_id, chapter_id, end_verse_number)
        REFERENCES verses (translation_id, chapter_id, verse_number)
        ON DELETE RESTRICT
);

CREATE INDEX idx_daily_readings_date_translation
    ON daily_readings (reading_date, translation_id);
