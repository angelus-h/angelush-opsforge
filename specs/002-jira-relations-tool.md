# Spec 002: Jira Ticket Relations & Dependency Graph (`tools/jira_relations.py`)

**Goal:** Automatically fetch relationships between Jira tickets (`blocks`, `is_blocked_by`, `relates_to`, `parent`, `subtasks`) from the Jira REST API, persist them in the SQLite `ticket_relations` table, and inject them into the Gemini context.

**Status:** Draft
**Date:** 2026-09-24
**Version:** 1.0.0

---

## 1. Motivation & Use Cases

- When working on a task (e.g., `SPRE-6318`), there is often a blocking prerequisite (e.g., `SPRE-4573` - firewall opening).
- Manually checking the status of related tickets is tedious.
- **The solution:**
  1. Read the `fields.issuelinks`, `fields.parent`, `fields.subtasks` fields when querying the Jira API.
  2. Save the relations into the SQLite database (`ticket_relations`).
  3. Automatically warn about open blocking tickets in the Streamlit UI and the AI prompt.

---

## 2. API and Command Line Interface

```bash
# Sync all relations of a ticket from the Jira API
python3 tools/jira_relations.py sync SPRE-6318

# Display dependency tree in the terminal
python3 tools/jira_relations.py tree SPRE-6318
```

---

## 3. UI Display

- Appears in the "🔗 Related Tickets & Dependencies" section of Streamlit:
  - 🔴 Blocking, unclosed tickets.
  - 🟢 Closed prerequisites.
  - Direct clickable Jira links.
