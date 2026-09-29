"""
SQLite Database Layer for SRE-Hub (Spec 001).
Provides thread-safe connections, WAL mode, schema management, and FTS5 search.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "state" / "investigations.db"

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 1. Main investigations table
CREATE TABLE IF NOT EXISTS investigations (
    jira_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    investigation_type TEXT NOT NULL DEFAULT 'Implementation',
    contract_md TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Ticket relations and dependency graph
CREATE TABLE IF NOT EXISTS ticket_relations (
    source_key TEXT NOT NULL,
    target_key TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    summary TEXT DEFAULT '',
    status TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_key, target_key, relation_type),
    FOREIGN KEY (source_key) REFERENCES investigations(jira_key) ON DELETE CASCADE
);

-- 3. Investigation artifacts (reviews, logs, diffs)
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jira_key TEXT NOT NULL,
    artifact_name TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(jira_key, artifact_name),
    FOREIGN KEY (jira_key) REFERENCES investigations(jira_key) ON DELETE CASCADE
);

-- 4. Persistent AI chat history
CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jira_key TEXT NOT NULL,
    model_name TEXT NOT NULL,
    role TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (jira_key) REFERENCES investigations(jira_key) ON DELETE CASCADE
);

-- 5. FTS5 full-text search table
CREATE VIRTUAL TABLE IF NOT EXISTS investigations_fts USING fts5(
    jira_key UNINDEXED,
    title,
    contract_md,
    content='investigations',
    content_rowid='rowid'
);

-- Triggers for FTS5 synchronization
CREATE TRIGGER IF NOT EXISTS investigations_ai AFTER INSERT ON investigations BEGIN
    INSERT INTO investigations_fts(rowid, jira_key, title, contract_md)
    VALUES (new.rowid, new.jira_key, new.title, new.contract_md);
END;

CREATE TRIGGER IF NOT EXISTS investigations_ad AFTER DELETE ON investigations BEGIN
    INSERT INTO investigations_fts(investigations_fts, rowid, jira_key, title, contract_md)
    VALUES ('delete', old.rowid, old.jira_key, old.title, old.contract_md);
END;

CREATE TRIGGER IF NOT EXISTS investigations_au AFTER UPDATE ON investigations BEGIN
    INSERT INTO investigations_fts(investigations_fts, rowid, jira_key, title, contract_md)
    VALUES ('delete', old.rowid, old.jira_key, old.title, old.contract_md);
    INSERT INTO investigations_fts(rowid, jira_key, title, contract_md)
    VALUES (new.rowid, new.jira_key, new.title, new.contract_md);
END;
"""


def get_db_path(custom_path: Optional[Path] = None) -> Path:
    if custom_path:
        return Path(custom_path)
    env_path = os.getenv("SRE_HUB_DB_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target_path = get_db_path(db_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Initializes the database schema if tables do not exist."""
    conn = get_db_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db(db_path: Optional[Path] = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager yielding an active database connection with auto-commit/rollback."""
    conn = get_db_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
