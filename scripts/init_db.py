#!/usr/bin/env python3
"""Initialize LitBot SQLite database schema."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "litbot.db"

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

-- Canonical paper identity layer
CREATE TABLE IF NOT EXISTS papers (
    pid         TEXT PRIMARY KEY,
    doi         TEXT UNIQUE,
    arxiv_id    TEXT UNIQUE,
    s2_id       TEXT UNIQUE,
    openalex_id TEXT UNIQUE,
    title       TEXT NOT NULL,
    authors     TEXT,                      -- JSON: ["Last, First", ...]
    year        INTEGER,
    venue       TEXT,
    abstract    TEXT,
    embedding   BLOB,                     -- SPECTER2 768-dim float32
    concepts    TEXT,                      -- JSON: ["concept1", ...]
    citation_count INTEGER DEFAULT 0,
    pdf_url     TEXT,
    user_id     TEXT DEFAULT 'default',    -- future multi-user support
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_papers_title ON papers(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_papers_user ON papers(user_id);
CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);

-- User interaction history
CREATE TABLE IF NOT EXISTS interactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pid         TEXT NOT NULL REFERENCES papers(pid),
    action      TEXT NOT NULL CHECK(action IN ('save','mute','click','dismiss','thumbs_up','thumbs_down')),
    context     TEXT,                      -- 'F1' | 'F2' | 'F3' | 'bootstrap' | ...
    user_id     TEXT DEFAULT 'default',
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_interactions_pid ON interactions(pid);
CREATE INDEX IF NOT EXISTS idx_interactions_action ON interactions(action);
CREATE INDEX IF NOT EXISTS idx_interactions_user ON interactions(user_id);

-- Push history (idempotent delivery)
CREATE TABLE IF NOT EXISTS pushes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pid         TEXT NOT NULL REFERENCES papers(pid),
    function    TEXT NOT NULL,              -- 'F1' | 'F2' | 'F3'
    message_id  TEXT,                      -- Feishu message_id for card update
    user_id     TEXT DEFAULT 'default',
    pushed_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(pid, function, user_id)
);

-- Feishu callback state (idempotent)
CREATE TABLE IF NOT EXISTS callbacks (
    callback_id TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL,
    action      TEXT NOT NULL,
    processed   INTEGER DEFAULT 0 CHECK(processed IN (0, 1)),
    created_at  TEXT DEFAULT (datetime('now'))
);

-- Daily scan state (resumable)
CREATE TABLE IF NOT EXISTS scan_state (
    source      TEXT PRIMARY KEY,           -- 'crossref' | 'arxiv' | 'openalex'
    last_cursor TEXT,
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- Trend statistics
CREATE TABLE IF NOT EXISTS trend_stats (
    concept     TEXT NOT NULL,
    date        TEXT NOT NULL,
    count       INTEGER NOT NULL,
    PRIMARY KEY (concept, date)
);

-- Observability log
CREATE TABLE IF NOT EXISTS op_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT DEFAULT (datetime('now')),
    source      TEXT,
    operation   TEXT,
    status      TEXT CHECK(status IN ('ok','error','timeout','circuit_break')),
    latency_ms  INTEGER,
    tokens_used INTEGER,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_op_log_ts ON op_log(ts);
CREATE INDEX IF NOT EXISTS idx_op_log_source ON op_log(source);

-- Bootstrap tracking
CREATE TABLE IF NOT EXISTS bootstrap_state (
    user_id     TEXT PRIMARY KEY DEFAULT 'default',
    mode        TEXT DEFAULT 'active' CHECK(mode IN ('active','completed')),
    seed_count  INTEGER DEFAULT 0,         -- papers used as seed
    save_count  INTEGER DEFAULT 0,         -- user saves so far
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);
"""


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Create database and tables. Returns connection."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO bootstrap_state (user_id) VALUES ('default')"
    )
    conn.commit()
    return conn


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Get database connection. Creates DB if not exists."""
    path = db_path or DB_PATH
    if not path.exists():
        return init_db(path)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


if __name__ == "__main__":
    conn = init_db()
    print(f"Database initialized at {DB_PATH}")
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    print(f"Tables: {', '.join(t[0] for t in tables)}")
    conn.close()
