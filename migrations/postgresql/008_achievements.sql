-- PostgreSQL schema migration; no data is seeded in this stage.
-- Source schema: 008_achievements.sql

CREATE TABLE achievement_unlocks (
    owner_key TEXT NOT NULL DEFAULT 'local-installation',
    achievement_code TEXT NOT NULL,
    unlocked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_key, achievement_code),
    CHECK (length(trim(owner_key)) > 0),
    CHECK (length(trim(achievement_code)) > 0)
);

CREATE TABLE weekly_goal_achievements (
    owner_key TEXT NOT NULL DEFAULT 'local-installation',
    week_start DATE NOT NULL,
    weekly_target INTEGER NOT NULL CHECK (weekly_target BETWEEN 1 AND 7),
    achieved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_key, week_start),
    CHECK (length(trim(owner_key)) > 0)
);

CREATE INDEX idx_achievement_unlocks_owner
    ON achievement_unlocks (owner_key, unlocked_at DESC);
