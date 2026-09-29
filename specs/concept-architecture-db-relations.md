# Concept and System Design: NoSQL Data Layer, Relation Graph, and New SRE Tools

**Status:** Draft / Under Maturation
**Date:** 2026-09-24
**Project:** SRE-Hub (`/home/mgreczi/ai/konflux-lumino/projects/sre-hub`)

---

## 1. Why Deviate from Classic SQL? (NoSQL / Document & Graph Approach)

The data of SRE-Hub fundamentally **does not fit well into relational tables**, but rather:
1. **Flexible Documents (Schemaless Documents):**
   - Every Jira ticket, GitLab MR analysis, Slack thread, and Tekton run has different metadata structures and dynamic sections.
   - With a rigid SQL schema, adding any new tool or new field would require migrations (`ALTER TABLE`).
2. **Network Relationships (Graph & Relations):**
   - An investigation is essentially a node connected in multiple directions:
     - Jira `issuelinks`: *blocks*, *is blocked by*, *relates to*, *duplicates*, *causes*.
     - Parent/Child: Epic -> Ticket -> Subtask.
     - External entities: GitLab MRs, Tekton PipelineRuns, OpenShift Pods, ServiceNow Change/Incident tickets.

### Analysis of NoSQL Alternatives (Embedded / Local Environment)

We do not want to run heavy background processes or a separate database server (e.g., a dedicated MongoDB server). The goal is a **lightweight, local, embedded** operation:

| Approach | Technology | Pros | Cons |
| :--- | :--- | :--- | :--- |
| **A) Embedded Document Store** | **TinyDB** | Pure Python, JSON file-based, zero configuration, schemaless documents. | May slow down with large datasets (>50k docs), no built-in graph traversal. |
| **B) JSON-Document Engine** | **SQLite JSON (JSON1)** | Single `.db` file, lightning-fast C engine, full transaction management, schemaless JSON column (`payload JSON`), FTS5 full-text search. | Requires SQL syntax for JSON operators, though it can be entirely hidden in Python. |
| **C) Embedded Graph DB** | **KùzuDB** or **NetworkX + JSON** | Native handling of nodes and edges (Jira links, MRs, Pods). Cypher query language. | Extra C++ dependency (Kùzu); NetworkX requires manual serialization. |
| **D) Vector Document Store** | **LanceDB** / **ChromaDB** | Local, embedded, document store + built-in semantic search (discovering similar incidents). | Larger library footprint. |

### Proposed Hybrid NoSQL Direction:
**Document Store + Graph Relations (Embedded Document-Graph):**
- **Document Layer:** The state of tickets and downloaded analyses are stored as flexible JSON/Markdown documents (e.g., in SQLite JSONB / TinyDB).
- **Relational Graph:** Relationships between Jira tickets (`blocks`, `relates_to`, `subtask_of`, `mr_link`) are stored as edges, allowing the entire dependency tree to be queried.

---

## 2. Managing Related Jira Tickets (Related Issues)

### The Problem:
An SRE investigation rarely lives in a vacuum. Behind an issue (`SPRE-6318`), there is often:
- A blocking task (`SPRE-4573`).
- A parent Epic that defines broader business goals.
- Previous, duplicated, or related bug tickets that might already contain the key to the solution.

### The Concept:
1. **Automatic Discovery of Relationships (Jira API fetch):**
   - When the system fetches the ticket via `jira_gemini.py` or the Tracker, it also processes the `fields.issuelinks`, `fields.parent`, and `fields.subtasks` fields:
     ```json
     "relations": [
       {"type": "blocks", "key": "SPRE-6500", "summary": "...", "status": "Open"},
       {"type": "is_blocked_by", "key": "SPRE-4573", "summary": "...", "status": "In Progress"},
       {"type": "relates_to", "key": "KFLUXINFRA-2857", "summary": "...", "status": "Closed"}
     ]
     ```
2. **Multi-Ticket Context for the LLM:**
   - The AI sees not only the description of the current ticket but also a concise summary of related tickets.
   - **Example inference:** *"Warning: The implementation of SPRE-6318 depends on SPRE-4573, which is currently 'In Progress'. You can only deploy the Datadog proxy settings after the Squid proxy access is opened."*
3. **Visual Relationship Map on the UI:**
   - An interactive relationship graph (e.g., Network graph or tree structure) in the Streamlit sidebar or main page:
     - Red: Blocking, open ticket.
     - Green: Closed dependency.
     - Blue: GitLab Merge Request.

---

## 3. System Design of New Tools

### A) OpenShift Tool (`tools/openshift_tool.py`)
- **Goal:** Immediate filtering and sanitization of failing pods, deployments, and events.
- **Capabilities:**
  - `diagnose_pod(namespace, pod_name)`: Fetches the last 50 lines and the exit code (`OOMKilled`, `CrashLoopBackOff`) of the crashed container.
  - `get_recent_errors(namespace)`: Filters Warning/Failed events from the last 30 minutes.
  - Automatic token/password masking via `log_sanitizer.py`.

### B) Tekton / Konflux Pipeline Tool (`tools/tekton_tool.py`)
- **Goal:** Extract the failed step from massive (ten-thousand-line) CI/CD pipeline runs.
- **Capabilities:**
  - `analyze_pipelinerun(name, namespace)`: Scans the `TaskRun`s, finding the first failed task (`exitCode != 0`).
  - Passes only the log of the failed step (`step-...`) to the analyzer, saving 95% of unnecessary tokens.

### C) ServiceNow Tool (`tools/servicenow_tool.py`)
- **Goal:** Checking Change Management (CHG) and Incidents (INC).
- **Capabilities:**
  - `check_change_window(chg_number)`: Checks if the maintenance window is open and the CHG is approved.
  - `link_incident(inc_number, jira_key)`: Links the customer-side incident with the internal SRE bug ticket.

---

## 4. Next Steps for Maturation (Spec-Driven Workflow)

1. **Decision on database technology:**
   - Should we experiment with a lightweight, pure JSON-based document store (e.g., TinyDB or SQLite-JSON) that runs instantly and seamlessly in Python without an external server?
2. **Approval of specification:**
   - Once the concept is solidified, we will create the first implementation specification (`specs/001-document-store-and-relations.md`).
