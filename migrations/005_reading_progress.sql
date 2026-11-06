CREATE TABLE reading_completions (
    id INTEGER PRIMARY KEY,
    owner_key TEXT NOT NULL DEFAULT 'local-installation',
    daily_reading_id INTEGER NOT NULL
        REFERENCES daily_readings(id) ON DELETE RESTRICT,
    completed_date TEXT NOT NULL
        CHECK (completed_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    completed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (length(trim(owner_key)) > 0),
    UNIQUE (owner_key, daily_reading_id),
    UNIQUE (owner_key, completed_date)
);

CREATE INDEX idx_reading_completions_owner_date
    ON reading_completions (owner_key, completed_date DESC, id DESC);

CREATE TABLE reading_goals (
    owner_key TEXT PRIMARY KEY DEFAULT 'local-installation',
    weekly_target INTEGER NOT NULL CHECK (weekly_target BETWEEN 1 AND 7),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (length(trim(owner_key)) > 0)
);
