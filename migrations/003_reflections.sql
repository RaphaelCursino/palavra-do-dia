CREATE TABLE reflections (
    id INTEGER PRIMARY KEY,
    daily_reading_id INTEGER NOT NULL UNIQUE
        REFERENCES daily_readings(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    reflection_text TEXT NOT NULL,
    simple_message TEXT NOT NULL,
    practical_application TEXT NOT NULL,
    prayer_text TEXT NOT NULL,
    reflection_question TEXT NOT NULL,
    author TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published')),
    published_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (length(trim(title)) > 0),
    CHECK (length(trim(reflection_text)) > 0),
    CHECK (length(trim(simple_message)) > 0),
    CHECK (length(trim(practical_application)) > 0),
    CHECK (length(trim(prayer_text)) > 0),
    CHECK (length(trim(reflection_question)) > 0),
    CHECK (length(trim(author)) > 0),
    CHECK (
        (status = 'draft' AND published_at IS NULL)
        OR
        (status = 'published' AND published_at IS NOT NULL
            AND length(trim(published_at)) > 0)
    )
);

CREATE INDEX idx_reflections_status_published
    ON reflections (status, published_at DESC, id DESC);
