# Spec 001: SQLite Investigation Store & Hybrid Storage Engine

**Goal:** Replace the filesystem-based investigation engine of SRE-Hub with a high-performance, embedded SQLite database, featuring persistent chat history, Jira relations, FTS5 full-text search, and hybrid file synchronization.

**Document Status:** Ready for Approval
**Date:** 2026-09-24
**Version:** 1.0.0
**Affected Files:**
- `core/db.py` (New database module)
- `core/investigation_tracker.py` (Refactored SQLite engine maintaining the exact same public API)
- `tools/migrate_filesystem_to_db.py` (One-time migration utility)
- `state/investigations.db` (Database file, ignored in `.gitignore`)

---

## 1. Motivation and Design Principles

### Problems with the current model:
1. **Slow directory traversal:** `list_investigations()` iterates through 70+ folders on disk, reads `mtime`, and filters with regex on every page refresh.
2. **Lost chat history:** Dialogues with the AI only live in `st.session_state` and are lost upon page refresh.
3. **Lack of searchability:** It is impossible to quickly search for solutions in past incidents based on keywords.
4. **Missing relations:** Relationships between Jira tickets (`blocks`, `is_blocked_by`, `relates_to`, `parent`) are not stored structurally.

### Solution Principles:
- **Zero New Dependencies:** Uses exclusively Python's built-in `sqlite3` module (no external database daemon or new pip packages).
- **Unchanged Public API:** All existing public methods of `InvestigationTracker` (`create_investigation`, `read_investigation`, `save_artifact`, `list_investigations`, `interact_with_ai`) continue to work with unchanged parameters, minimizing impact on `app.py`.
- **Raw Markdown Document Store:** The contract state (`analysis.md`) is stored in a single raw `TEXT` field in the database, avoiding fragile field extractions.
- **Hybrid Synchronization:** SQLite is the Single Source of Truth, but every save automatically updates the `analysis.md` file on disk as well, ensuring it remains directly accessible from the terminal (`cat`, `vim`) or OpenCode CLI.

---

## 2. Database Schema (DDL)

Database file location: `state/investigations.db` (created automatically if it doesn't exist).

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 1. Main investigations table
CREATE TABLE IF NOT EXISTS investigations (
    jira_key TEXT PRIMARY KEY,          -- e.g., SPRE-6318
    title TEXT NOT NULL,                 -- Ticket title / topic
    status TEXT NOT NULL DEFAULT 'ACTIVE', -- 'ACTIVE', 'RESOLVED', 'BLOCKED'
    investigation_type TEXT NOT NULL,    -- 'Incident', 'Implementation', 'Architecture'
    contract_md TEXT NOT NULL,           -- Full raw markdown content
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Linked tickets and dependency graph
CREATE TABLE IF NOT EXISTS ticket_relations (
    source_key TEXT NOT NULL,           -- e.g., SPRE-6318
    target_key TEXT NOT NULL,           -- e.g., SPRE-4573
    relation_type TEXT NOT NULL,        -- 'blocks', 'is_blocked_by', 'relates_to', 'parent', 'subtask'
    summary TEXT DEFAULT '',            -- Short description of the target ticket
    status TEXT DEFAULT '',             -- Status of the target ticket (Open, In Progress, Closed)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_key, target_key, relation_type),
    FOREIGN KEY (source_key) REFERENCES investigations(jira_key) ON DELETE CASCADE
);

-- 3. Attached artifacts
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jira_key TEXT NOT NULL,
    artifact_name TEXT NOT NULL,         -- e.g., 'gitlab_review.md', 'tekton_failure.log'
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(jira_key, artifact_name),
    FOREIGN KEY (jira_key) REFERENCES investigations(jira_key) ON DELETE CASCADE
);

-- 4. Persistent AI Chat History
CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jira_key TEXT NOT NULL,
    model_name TEXT NOT NULL,            -- e.g., 'gemini-3.6-flash'
    role TEXT NOT NULL,                  -- 'user' or 'assistant'
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (jira_key) REFERENCES investigations(jira_key) ON DELETE CASCADE
);

-- 5. FTS5 Full-Text Search Virtual Table
CREATE VIRTUAL TABLE IF NOT EXISTS investigations_fts USING fts5(
    jira_key UNINDEXED,
    title,
    contract_md,
    content='investigations',
    content_rowid='rowid'
);

-- Triggers to keep the FTS index in sync automatically
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
```

---

## 3. Components and Responsibilities

### A) `core/db.py` (Database Connection and Core Manager)
- `get_db_connection() -> sqlite3.Connection`: WAL mode, `row_factory = sqlite3.Row`, enables foreign keys.
- `init_db()`: Creates the tables and triggers above if they do not exist.

### B) `core/investigation_tracker.py` (Refactored Tracker)
- **`list_investigations(search_query: str = None) -> List[str]`**:
  - Default: `SELECT jira_key FROM investigations ORDER BY updated_at DESC`.
  - With search query: `SELECT jira_key FROM investigations_fts WHERE investigations_fts MATCH ?`.
  - Lightning fast, 0ms disk I/O.
- **`read_investigation(jira_key: str) -> Optional[str]`**:
  - `SELECT contract_md FROM investigations WHERE jira_key = ?`.
  - If not in DB, checks the disk (fallback), and if found, immediately pulls it into the DB.
- **`save_artifact(jira_key: str, filename: str, content: str)`**:
  - `INSERT OR REPLACE INTO artifacts ...`
  - Hybrid save: also writes to the ticket's directory on disk (`investigations/detailed/<KEY>/<filename>`).
- **`interact_with_ai(jira_key: str, user_input: str, model_name: str = "auto")`**:
  - Saves `user_input` to `chat_history`.
  - Generates the LLM response.
  - Updates `contract_md` and `chat_history` in a single transaction.
  - Writes the new state to disk as well.
- **New methods:**
  - `get_chat_history(jira_key: str) -> List[Dict]`: Returns the persisted conversation.
  - `add_ticket_relation(source_key, target_key, relation_type, summary, status)`
  - `get_ticket_relations(jira_key: str) -> List[Dict]`

### C) `tools/migrate_filesystem_to_db.py` (Migration Utility)
- Traverses the `investigations/detailed/` directory.
- Identifies the existing 70+ real Jira investigations.
- Reads `analysis.md` and all `.md` artifacts on disk.
- Populates the `state/investigations.db` database in a single transaction.
- Logs the number of migrated tickets and artifacts.

---

## 4. Acceptance Criteria

1. [ ] The `state/investigations.db` is created and initialized with tables and triggers.
2. [ ] The `migrate_filesystem_to_db.py` successfully copies existing 70+ investigations (e.g., `SPRE-6318`) and their artifacts.
3. [ ] `list_investigations()` runs in under 1 millisecond using SQL queries, non-Jira folders (e.g., `gitlab_mr_422`) remain excluded.
4. [ ] The FTS5 engine instantly finds terms like "squid proxy" or "datadog" among past investigations.
5. [ ] Upon updating an investigation, both SQLite and the on-disk `analysis.md` file are updated (hybrid consistency).
6. [ ] Conversations within Streamlit reload from `chat_history` even after a page refresh (F5).
