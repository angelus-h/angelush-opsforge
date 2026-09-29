"""
core/investigation_tracker.py
Deterministic state manager and AI updater for long-running SRE investigations.
Maintains a strict 4-section Markdown contract:
  1. CONFIRMED FACTS
  2. RULED OUT
  3. ACTIVE HYPOTHESES & NEXT EXPERIMENTS
  4. TIMELINE & EXPERIMENT LOG
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional
import re

from config import INVESTIGATIONS_DIR, JIRA_URL
from core.llm_bridge import LLMBridge
from core.db import get_db, init_db


INVESTIGATION_TEMPLATE = """# JIRA INVESTIGATION: {jira_key} - {title}
**Jira Issue:** [{jira_key}]({jira_url}/browse/{jira_key})
**Type:** Investigation (RCA)
**Status:** {status}
**Target Repo / System:** {target_system}
**Created:** {created_at}
**Last Updated:** {last_updated}

## 1. CONFIRMED FACTS
<!-- Evidence-based facts verified via logs, metrics, or code inspection -->
{facts}

## 2. RULED OUT
<!-- Dead ends and disproven hypotheses to avoid looping back -->
{ruled_out}

## 3. ACTIVE HYPOTHESES & NEXT EXPERIMENTS
<!-- Current working theories and targeted actions -->
{hypotheses}

## 4. TIMELINE & EXPERIMENT LOG
<!-- Append-only chronological trace of actions and findings -->
{timeline}
"""

IMPLEMENTATION_TEMPLATE = """# JIRA IMPLEMENTATION: {jira_key} - {title}
**Jira Issue:** [{jira_key}]({jira_url}/browse/{jira_key})
**Type:** Implementation (Feature/Fix)
**Status:** {status}
**Target Repo / System:** {target_system}
**Created:** {created_at}
**Last Updated:** {last_updated}

## 1. SPEC & HARD CONSTRAINTS
<!-- Requirements, API versions, environmental boundaries, non-negotiables -->
{specs}

## 2. CURRENT WORKING CODE BASELINE
<!-- Last known working code snippet, task, or configuration -->
{baseline}

## 3. TRIED & FAILED ATTEMPTS
<!-- Discarded approaches, error logs, and why they failed (prevents regressions) -->
{failed_attempts}

## 4. IMMEDIATE NEXT EXPERIMENT & CODE SNIPPET
<!-- Next testable change, code snippet, or command to run -->
{next_step}
"""

# Keep backward-compatibility alias
DEFAULT_TEMPLATE = INVESTIGATION_TEMPLATE


class InvestigationTracker:
    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or INVESTIGATIONS_DIR
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        try:
            init_db()
        except Exception as e:
            print(f"Warning: Failed to initialize SQLite database: {e}")

    def get_ticket_dir(self, jira_key: str) -> Path:
        """Returns the dedicated directory path for a specific Jira ticket (case-insensitive fallback)."""
        safe_id = re.sub(r"[^A-Za-z0-9_\-\.]", "_", jira_key.strip().upper())
        exact = self.storage_dir / safe_id
        if exact.is_dir():
            return exact
        # Case-insensitive resolution for lowercase directories like gitlab_mr_422
        if self.storage_dir.exists():
            for item in self.storage_dir.iterdir():
                if item.is_dir() and item.name.upper() == safe_id:
                    return item
        return exact

    def get_file_path(self, jira_key: str) -> Path:
        """
        Locates or sets the primary contract file for a Jira ticket.
        Priority:
          1. <storage_dir>/<TICKET>/analysis.md
          2. <storage_dir>/<TICKET>/<TICKET>.md
          3. <storage_dir>/<TICKET>/*analysis*.md (e.g. mr_analysis_*.md)
          4. <storage_dir>/<TICKET>.md (flat legacy file)
          5. Default for new creations: <storage_dir>/<TICKET>/analysis.md
        """
        safe_id = re.sub(r"[^A-Za-z0-9_\-\.]", "_", jira_key.strip().upper())
        ticket_dir = self.get_ticket_dir(jira_key)

        if ticket_dir.is_dir():
            analysis_file = ticket_dir / "analysis.md"
            if analysis_file.exists():
                return analysis_file
            legacy_sub_file = ticket_dir / f"{safe_id}.md"
            if legacy_sub_file.exists():
                return legacy_sub_file
            # Check case-insensitively for {item.name}.md
            for sub_f in ticket_dir.glob("*.md"):
                if sub_f.stem.upper() == safe_id:
                    return sub_f
            # Check for existing analysis markdown files (e.g., gitlab mr_analysis_*.md)
            analysis_candidates = sorted(
                list(ticket_dir.glob("*analysis*.md")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if analysis_candidates:
                return analysis_candidates[0]

        flat_file = self.storage_dir / f"{safe_id}.md"
        if flat_file.exists():
            return flat_file

        # Default standard path
        return ticket_dir / "analysis.md"

    def exists(self, jira_key: str) -> bool:
        return self.get_file_path(jira_key).exists()

    def list_investigations(self, search_query: Optional[str] = None) -> List[str]:
        """
        List all saved Jira investigations.
        Uses SQLite (and FTS5 full-text search if query provided).
        Falls back to filesystem if DB has no results.
        """
        try:
            with get_db() as conn:
                if search_query and search_query.strip():
                    q = search_query.strip()
                    clean_q = re.sub(r"[^\w\s\-]", " ", q).strip()
                    if clean_q:
                        fts_query = f'"{clean_q}"*'
                        rows = conn.execute(
                            "SELECT jira_key FROM investigations_fts WHERE investigations_fts MATCH ? ORDER BY rank",
                            (fts_query,),
                        ).fetchall()
                        return [r[0] for r in rows]

                rows = conn.execute(
                    "SELECT jira_key FROM investigations ORDER BY updated_at DESC"
                ).fetchall()
                if rows:
                    return [r[0] for r in rows]
        except Exception:
            pass

        return self._list_investigations_from_fs()

    def _list_investigations_from_fs(self) -> List[str]:
        """Fallback filesystem scanner."""
        keys = []
        seen = set()

        if self.storage_dir.exists():
            for item in self.storage_dir.iterdir():
                if item.is_dir():
                    if item.name.lower().startswith(("gitlab_mr_", "slack_", "local_")):
                        continue

                    has_contract = (item / "analysis.md").exists()
                    is_jira_like = bool(re.match(r"^[A-Za-z0-9]+-\d+", item.name))
                    has_other_md = any(item.glob("*.md"))

                    if (
                        is_jira_like
                        or has_contract
                        or (has_other_md and not item.name.startswith("."))
                    ):
                        name = item.name.upper()
                        if name not in seen:
                            seen.add(name)
                            target_file = item / "analysis.md"
                            mtime = (
                                target_file.stat().st_mtime
                                if target_file.exists()
                                else item.stat().st_mtime
                            )
                            keys.append((name, mtime))
                elif item.is_file() and item.suffix == ".md":
                    name = item.stem.upper()
                    if name not in seen and bool(re.match(r"^[A-Za-z0-9]+-\d+", name)):
                        seen.add(name)
                        keys.append((name, item.stat().st_mtime))

        keys.sort(key=lambda x: x[1], reverse=True)
        return [k[0] for k in keys]

    def save_artifact(self, jira_key: str, filename: str, content: str) -> Path:
        """
        Saves an artifact (logs, architecture map, Slack trace, PagerDuty payload)
        directly inside the ticket's dedicated directory and syncs to SQLite.
        """
        safe_key = jira_key.strip().upper()
        ticket_dir = self.get_ticket_dir(safe_key)
        ticket_dir.mkdir(parents=True, exist_ok=True)
        target = ticket_dir / filename
        target.write_text(content, encoding="utf-8")

        try:
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO artifacts (jira_key, artifact_name, content, created_at)
                    VALUES (?, ?, ?, datetime('now'))
                    ON CONFLICT(jira_key, artifact_name) DO UPDATE SET
                        content = excluded.content
                    """,
                    (safe_key, filename, content),
                )
        except Exception as e:
            print(f"Warning: Failed to save artifact in DB: {e}")

        return target

    def list_artifacts(self, jira_key: str) -> List[Path]:
        """Returns all artifact files stored in the ticket's directory, excluding the main contract."""
        ticket_dir = self.get_ticket_dir(jira_key)
        if not ticket_dir.exists() or not ticket_dir.is_dir():
            return []
        main_file = self.get_file_path(jira_key)
        return [
            p
            for p in ticket_dir.iterdir()
            if p.is_file() and p.resolve() != main_file.resolve()
        ]

    def create_investigation(
        self,
        incident_id: str,
        title: str = "",
        target_system: str = "Unspecified",
        mode: str = "investigation",
        initial_fact: str = "",
        initial_hypothesis: str = "",
        jira_url: Optional[str] = None,
        scan_directory: bool = True,
    ) -> str:
        """Create a new deterministic investigation or implementation file scoped to a Jira ticket."""
        jira_key = incident_id.strip().upper()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        target_file = self.get_file_path(jira_key)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        base_jira_url = (jira_url or JIRA_URL).rstrip("/")

        # Check if existing directory has files to synthesize from
        if scan_directory and not target_file.exists():
            synthesized = self.synthesize_from_existing_directory(
                incident_id=jira_key,
                mode=mode,
                title=title if title.strip() else None,
                target_system=target_system if target_system != "Unspecified" else None,
                initial_fact=initial_fact,
                initial_hypothesis=initial_hypothesis,
                jira_url=base_jira_url,
            )
            if synthesized:
                return synthesized

        eff_title = title.strip() if title.strip() else f"{jira_key} SRE Task"

        if mode == "implementation":
            specs = (
                f"- [x] {initial_fact}"
                if initial_fact
                else "- [ ] Implement requirements as requested"
            )
            baseline = "- (No verified working baseline yet)"
            failed_attempts = "- (None so far)"
            next_step = (
                f"- [ ] Next: {initial_hypothesis}"
                if initial_hypothesis
                else "- [ ] Formulate first implementation iteration"
            )

            content = IMPLEMENTATION_TEMPLATE.format(
                jira_key=jira_key,
                jira_url=base_jira_url,
                title=eff_title,
                status="ACTIVE",
                target_system=target_system,
                created_at=now_str,
                last_updated=now_str,
                specs=specs,
                baseline=baseline,
                failed_attempts=failed_attempts,
                next_step=next_step,
            )
        else:
            facts = (
                f"- [x] {initial_fact}"
                if initial_fact
                else "- [ ] (No verified facts yet)"
            )
            ruled_out = "- (None so far)"
            hypotheses = (
                f"- [ ] Hypothesis: {initial_hypothesis}\n  - Experiment: Test initial assumption"
                if initial_hypothesis
                else "- [ ] (Formulate initial hypothesis)"
            )
            timeline = (
                f"- [{now_str}] Investigation initialized for {jira_key}: {eff_title}"
            )

            content = INVESTIGATION_TEMPLATE.format(
                jira_key=jira_key,
                jira_url=base_jira_url,
                title=eff_title,
                status="ACTIVE",
                target_system=target_system,
                created_at=now_str,
                last_updated=now_str,
                facts=facts,
                ruled_out=ruled_out,
                hypotheses=hypotheses,
                timeline=timeline,
            )

        self.save_raw_investigation(jira_key, content)
        return content

    def synthesize_from_existing_directory(
        self,
        incident_id: str,
        mode: Optional[str] = None,
        title: Optional[str] = None,
        target_system: Optional[str] = None,
        initial_fact: str = "",
        initial_hypothesis: str = "",
        jira_url: Optional[str] = None,
        model: str = "gemini-flash-latest",
    ) -> Optional[str]:
        """
        Scans an existing ticket directory for notes, plans, playbooks, or checklists,
        and synthesizes a complete 4-section state contract (analysis.md).
        """
        jira_key = incident_id.strip().upper()
        ticket_dir = self.get_ticket_dir(jira_key)
        if not ticket_dir.is_dir():
            return None

        # Exclude directories like vaults, .git, caches
        excluded_dirs = {"vaults", ".git", "__pycache__", "node_modules", ".venv"}
        inferred_title = title.strip() if title and title.strip() else ""
        inferred_system = (
            target_system.strip()
            if target_system
            and target_system.strip()
            and target_system != "Unspecified"
            else ""
        )

        # Priority search for markdown files first, then playbooks / configs
        md_files = sorted(
            list(ticket_dir.glob("*.md")),
            key=lambda p: (
                0 if "plan" in p.name.lower() or "readme" in p.name.lower() else 1,
                p.name,
            ),
        )
        other_files = [
            p
            for p in ticket_dir.rglob("*")
            if p.is_file()
            and not any(ex in p.parts for ex in excluded_dirs)
            and p.suffix in [".yml", ".yaml", ".py", ".sh", ".txt", ".json", ".csv"]
        ]

        all_candidates = md_files + other_files
        # Exclude target analysis file itself if already reading it
        all_candidates = [
            f for f in all_candidates if f.name not in ("analysis.md", f"{jira_key}.md")
        ]

        if not all_candidates:
            return None

        total_bytes = 0
        file_snippets = []

        for fpath in all_candidates:
            try:
                rel_path = fpath.relative_to(ticket_dir)
                txt = fpath.read_text(encoding="utf-8", errors="ignore")
                if not inferred_title and fpath.suffix == ".md":
                    # Try to extract title from first # Header
                    m = re.search(
                        r"^#\s+(?:[A-Za-z0-9_\-]+[:\-]\s*)?(.+)", txt, re.MULTILINE
                    )
                    if m:
                        inferred_title = m.group(1).strip()

                snippet = txt[:6000]
                total_bytes += len(snippet)
                file_snippets.append(f"### File: {rel_path}\n```\n{snippet}\n```")
                if total_bytes > 30000:
                    break
            except Exception:
                continue

        if not file_snippets:
            return None

        if not inferred_title:
            inferred_title = f"{jira_key} SRE Task / Investigation"

        materials_text = "\n\n".join(file_snippets)

        if not inferred_system:
            repo_match = re.search(
                r"(rhsm-[a-z0-9\-_]+|konflux-[a-z0-9\-_]+|pulp-[a-z0-9\-_]+|alertmanager|datadog[a-z0-9\-_]*)",
                materials_text,
                re.IGNORECASE,
            )
            if repo_match:
                inferred_system = repo_match.group(1)
        if not mode:
            impl_keywords = [
                "implementation",
                "playbook",
                "deploy",
                "ansible",
                "feature",
                "upgrade",
                "migration",
            ]
            rca_keywords = [
                "incident",
                "outage",
                "rca",
                "root cause",
                "failure",
                "alert",
                "pagerduty",
            ]
            impl_score = sum(materials_text.lower().count(k) for k in impl_keywords)
            rca_score = sum(materials_text.lower().count(k) for k in rca_keywords)
            mode = "implementation" if impl_score >= rca_score else "investigation"

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        base_jira_url = (jira_url or JIRA_URL).rstrip("/")
        target_file = ticket_dir / "analysis.md"

        user_hints = ""
        if initial_fact:
            user_hints += f"\n- Additional User Observation/Fact: {initial_fact}"
        if initial_hypothesis:
            user_hints += (
                f"\n- Additional User Next Step/Hypothesis: {initial_hypothesis}"
            )

        system_prompt = (
            "You are a Senior Principal SRE and Software Engineer. "
            "Your task is to synthesize existing directory documentation, code, and notes into a deterministic state contract markdown. "
            "Strictly follow the required 4-section schema without conversational filler."
        )

        if mode == "implementation":
            prompt = f"""Synthesize the provided project materials for Jira ticket {jira_key} into our standard 4-section Implementation Contract.

MATERIALS FROM WORKING DIRECTORY:
{materials_text}
{user_hints}

OUTPUT FORMAT REQUIREMENTS:
# JIRA IMPLEMENTATION: {jira_key} - {inferred_title}
**Jira Issue:** [{jira_key}]({base_jira_url}/browse/{jira_key})
**Type:** Implementation (Feature/Fix)
**Status:** ACTIVE
**Target Repo / System:** {inferred_system or "rhsm-pulp-playbooks"}
**Created:** {now_str}
**Last Updated:** {now_str}

## 1. SPEC & HARD CONSTRAINTS
- (List concrete requirements, constraints, network/proxy rules, versions extracted from materials)

## 2. CURRENT WORKING CODE BASELINE
- (List verified working components, tested hosts, playbook paths, working snippets)

## 3. TRIED & FAILED ATTEMPTS
- (List discarded approaches, failed wrappers, proxy hurdles, and reasons)

## 4. IMMEDIATE NEXT EXPERIMENT & CODE SNIPPET
- (List immediate actionable next steps, playbooks to run, or next code to write)

Output ONLY the markdown contract starting with '# JIRA IMPLEMENTATION:'.
"""
        else:
            prompt = f"""Synthesize the provided materials for Jira ticket {jira_key} into our standard 4-section Root Cause Analysis (RCA) Contract.

MATERIALS FROM WORKING DIRECTORY:
{materials_text}
{user_hints}

OUTPUT FORMAT REQUIREMENTS:
# JIRA INVESTIGATION: {jira_key} - {inferred_title}
**Jira Issue:** [{jira_key}]({base_jira_url}/browse/{jira_key})
**Type:** Investigation (RCA)
**Status:** ACTIVE
**Target Repo / System:** {inferred_system or "Unspecified"}
**Created:** {now_str}
**Last Updated:** {now_str}

## 1. CONFIRMED FACTS
- (Confirmed symptoms, verified log entries, error states)

## 2. RULED OUT
- (Ruled out causes or components)

## 3. ACTIVE HYPOTHESES & NEXT EXPERIMENTS
- (Leading theories and next verification steps)

## 4. TIMELINE & EXPERIMENT LOG
- [{now_str}] Synthesized from existing directory notes and materials.

Output ONLY the markdown contract starting with '# JIRA INVESTIGATION:'.
"""

        try:
            synthesized_md = LLMBridge.execute(
                prompt, model=model, system_prompt=system_prompt
            ).strip()
            required_h = (
                "## 1. SPEC & HARD CONSTRAINTS"
                if mode == "implementation"
                else "## 1. CONFIRMED FACTS"
            )
            if required_h in synthesized_md:
                target_file.write_text(synthesized_md, encoding="utf-8")
                return synthesized_md
        except Exception:
            pass

        # Fallback template if LLM is offline or output malformed
        file_summary = ", ".join(f.name for f in all_candidates[:5])
        if mode == "implementation":
            fallback_md = IMPLEMENTATION_TEMPLATE.format(
                jira_key=jira_key,
                jira_url=base_jira_url,
                title=inferred_title,
                status="ACTIVE",
                target_system=inferred_system or "Unspecified",
                created_at=now_str,
                last_updated=now_str,
                specs=f"- [x] Synthesized from existing directory files: {file_summary}\n"
                + (f"- {initial_fact}" if initial_fact else ""),
                baseline="- Existing code and notes available in ticket directory.",
                failed_attempts="- (None recorded yet)",
                next_step=f"- [ ] {initial_hypothesis}"
                if initial_hypothesis
                else "- [ ] Review discovered directory files.",
            )
        else:
            fallback_md = INVESTIGATION_TEMPLATE.format(
                jira_key=jira_key,
                jira_url=base_jira_url,
                title=inferred_title,
                status="ACTIVE",
                target_system=inferred_system or "Unspecified",
                created_at=now_str,
                last_updated=now_str,
                facts=f"- [x] Discovered existing directory files: {file_summary}\n"
                + (f"- {initial_fact}" if initial_fact else ""),
                ruled_out="- (None)",
                hypotheses=f"- [ ] Hypothesis: {initial_hypothesis}"
                if initial_hypothesis
                else "- [ ] Review existing directory artifacts.",
                timeline=f"- [{now_str}] Initialized from existing ticket materials.",
            )
        target_file.write_text(fallback_md, encoding="utf-8")
        return fallback_md

    def read_investigation(self, incident_id: str) -> str:
        """Read existing investigation file, checking SQLite first, then filesystem."""
        jira_key = incident_id.strip().upper()
        try:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT contract_md FROM investigations WHERE jira_key = ?",
                    (jira_key,),
                ).fetchone()
                if row and row[0]:
                    return row[0]
        except Exception:
            pass

        target_file = self.get_file_path(jira_key)
        if not target_file.exists():
            synthesized = self.synthesize_from_existing_directory(jira_key)
            if synthesized:
                self.save_raw_investigation(jira_key, synthesized)
                return synthesized
            raise FileNotFoundError(
                f"Investigation '{jira_key}' not found at {target_file}"
            )

        content = target_file.read_text(encoding="utf-8")
        self.save_raw_investigation(jira_key, content)
        return content

    def save_raw_investigation(self, incident_id: str, content: str) -> None:
        """Directly persist content to disk and SQLite (hybrid engine)."""
        jira_key = incident_id.strip().upper()
        target_file = self.get_file_path(jira_key)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(content, encoding="utf-8")

        title = f"{jira_key} Investigation"
        title_match = re.search(
            r"^#\s+(?:JIRA\s+[A-Za-z]+:\s*)?(?:[A-Za-z0-9]+-\d+\s*[-:]\s*)?([^\n]+)",
            content,
            re.MULTILINE,
        )
        if title_match and title_match.group(1).strip():
            title = title_match.group(1).strip()

        status = "ACTIVE"
        status_match = re.search(
            r"\*\*Status:\*\*\s*([A-Za-z_\-]+)", content, re.IGNORECASE
        )
        if status_match:
            status = status_match.group(1).strip().upper()

        inv_type = (
            "Implementation" if self.is_implementation_doc(content) else "Investigation"
        )

        try:
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO investigations (jira_key, title, status, investigation_type, contract_md, updated_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(jira_key) DO UPDATE SET
                        title = excluded.title,
                        status = excluded.status,
                        investigation_type = excluded.investigation_type,
                        contract_md = excluded.contract_md,
                        updated_at = datetime('now')
                    """,
                    (jira_key, title, status, inv_type, content),
                )
        except Exception as e:
            print(f"Warning: Failed to persist {jira_key} to SQLite: {e}")

    def get_chat_history(self, jira_key: str) -> List[dict]:
        """Fetch persistent chat history for a Jira ticket from SQLite."""
        safe_key = jira_key.strip().upper()
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT role, message, model_name, created_at FROM chat_history WHERE jira_key = ? ORDER BY id ASC",
                    (safe_key,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def save_chat_message(
        self, jira_key: str, role: str, message: str, model_name: str = "auto"
    ) -> None:
        """Saves a single chat message into persistent SQLite history."""
        safe_key = jira_key.strip().upper()
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO chat_history (jira_key, model_name, role, message) VALUES (?, ?, ?, ?)",
                    (safe_key, model_name, role, message),
                )
        except Exception as e:
            print(f"Warning: Failed to save chat message in DB: {e}")

    def add_ticket_relation(
        self,
        source_key: str,
        target_key: str,
        relation_type: str,
        summary: str = "",
        status: str = "",
    ) -> None:
        """Adds or updates a ticket relation link in SQLite."""
        s_key = source_key.strip().upper()
        t_key = target_key.strip().upper()
        try:
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO ticket_relations (source_key, target_key, relation_type, summary, status)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(source_key, target_key, relation_type) DO UPDATE SET
                        summary = excluded.summary,
                        status = excluded.status
                    """,
                    (s_key, t_key, relation_type, summary, status),
                )
        except Exception as e:
            print(f"Warning: Failed to add ticket relation in DB: {e}")

    def get_ticket_relations(self, jira_key: str) -> List[dict]:
        """Returns all related tickets and dependencies for a Jira ticket."""
        safe_key = jira_key.strip().upper()
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT target_key, relation_type, summary, status FROM ticket_relations WHERE source_key = ? ORDER BY created_at ASC",
                    (safe_key,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def is_implementation_doc(self, content: str) -> bool:
        """Detect whether document follows implementation schema or RCA schema."""
        return (
            "SPEC & HARD CONSTRAINTS" in content or "Type:** Implementation" in content
        )

    def update_with_ai(
        self,
        incident_id: str,
        observation: str,
        model: str = "gemini-flash-latest",
        context_attachment: str = "",
    ) -> str:
        """Legacy updater that returns only updated markdown content."""
        result = self.interact_with_ai(
            incident_id=incident_id,
            user_message=observation,
            model=model,
            context_attachment=context_attachment,
        )
        return result["updated_doc"]

    def interact_with_ai(
        self,
        incident_id: str,
        user_message: str,
        model: str = "gemini-flash-latest",
        context_attachment: str = "",
        mode: str = None,
        chat_history: List[dict] = None,
    ) -> dict:
        """
        Dual-output interaction:
        1. Returns a direct engineering answer/advice to the user with copy-pasteable commands.
        2. Deterministically updates and persists the 4-section state contract.
        Utilizes a 'Micro-Window' chat_history to preserve immediate conversational context.
        """
        current_content = self.read_investigation(incident_id)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if mode == "implementation":
            is_impl = True
        elif mode == "investigation":
            is_impl = False
        else:
            is_impl = self.is_implementation_doc(current_content)

        if is_impl:
            system_prompt = (
                "You are a Senior Principal SRE and Staff Software Engineer acting as an Implementation Assistant.\n"
                "You guide, advise, and help the engineer implement code, Ansible playbooks, or infrastructure changes incrementally.\n"
                "You are an ADVISOR and NAVIGATOR: always provide clear, copy-pasteable terminal commands or code snippets for the engineer to run themselves.\n"
                "Be token-efficient, direct, and zero-waste. Avoid polite filler or conversational preamble.\n\n"
                "To prevent infinite context growth and avoid repeating failed attempts, you maintain a STRICT 4-SECTION MARKDOWN CONTRACT:\n"
                "## 1. SPEC & HARD CONSTRAINTS\n"
                "## 2. CURRENT WORKING CODE BASELINE\n"
                "## 3. TRIED & FAILED ATTEMPTS\n"
                "## 4. IMMEDIATE NEXT EXPERIMENT & CODE SNIPPET\n\n"
                "OUTPUT FORMAT RULES:\n"
                "You MUST output exactly two blocks using delimiter tags:\n"
                "<<<RESPONSE>>>\n"
                "(Your direct engineering response to the user: answer questions, explain why the previous run failed, suggest the exact fix, and provide copy-paste commands/snippets. Take into account the recent conversation history provided.)\n"
                "<<<CONTRACT>>>\n"
                "(The entire updated Markdown contract faithfully preserved and updated with the latest state, updating '**Last Updated:**' to '{now_str}'.)\n\n"
                "RULES FOR CONTRACT:\n"
                "- If an attempt failed, append it to '## 3. TRIED & FAILED ATTEMPTS' with the root cause of failure so we never repeat it.\n"
                "- If an attempt succeeded, update '## 2. CURRENT WORKING CODE BASELINE'.\n"
                "- Put the next runnable code block or command in '## 4. IMMEDIATE NEXT EXPERIMENT & CODE SNIPPET'."
            )
        else:
            system_prompt = (
                "You are a Principal SRE managing an incident investigation and root cause analysis (RCA).\n"
                "You guide the investigation step-by-step: analyze symptoms, hypothesize root causes, and propose copy-paste diagnostic commands for the engineer to execute.\n"
                "Be token-efficient, direct, and zero-waste. Avoid polite filler or conversational preamble.\n\n"
                "You maintain a STRICT, DETERMINISTIC Markdown schema with EXACTLY these 4 sections:\n"
                "## 1. CONFIRMED FACTS\n"
                "## 2. RULED OUT\n"
                "## 3. ACTIVE HYPOTHESES & NEXT EXPERIMENTS\n"
                "## 4. TIMELINE & EXPERIMENT LOG\n\n"
                "OUTPUT FORMAT RULES:\n"
                "You MUST output exactly two blocks using delimiter tags:\n"
                "<<<RESPONSE>>>\n"
                "(Your direct engineering response: answer the engineer's prompt, analyze logs/symptoms, and give copy-paste terminal verification commands. Take into account the recent conversation history provided.)\n"
                "<<<CONTRACT>>>\n"
                "(The entire updated Markdown contract faithfully preserved, updating '**Last Updated:**' to '{now_str}' and adding a timeline entry.)\n\n"
                "RULES FOR CONTRACT:\n"
                "- When a hypothesis is disproven, MOVE IT to '## 2. RULED OUT' with reason.\n"
                "- When verified evidence emerges, add to '## 1. CONFIRMED FACTS'.\n"
                "- Update '## 3. ACTIVE HYPOTHESES & NEXT EXPERIMENTS' with 1-3 crisp next steps.\n"
                "- Always append an entry to '## 4. TIMELINE & EXPERIMENT LOG' with [{now_str}]."
            )

        # Save user message to persistent DB history
        self.save_chat_message(incident_id, "user", user_message, model_name=model)

        # Build Micro-Window Chat History (fallback to DB if session state is empty)
        history_str = ""
        effective_history = chat_history or []
        if not effective_history:
            db_history = self.get_chat_history(incident_id)
            if db_history:
                effective_history = [
                    {"role": h["role"], "content": h["message"]}
                    for h in db_history[-6:]
                ]

        if effective_history:
            history_str = "# RECENT CONVERSATION HISTORY (MICRO-WINDOW):\n"
            for msg in effective_history:
                role = "USER" if msg.get("role") == "user" else "ASSISTANT"
                history_str += f"[{role}]: {msg.get('content')}\n"
            history_str += "\n"

        # Scan working directory for attached artifacts (e.g. gitlab_review.md, PLAN.md, etc.)
        ticket_dir = self.get_ticket_dir(incident_id)
        artifacts_context = ""
        if ticket_dir.is_dir():
            art_snippets = []
            for art in sorted(ticket_dir.glob("*.md")):
                if art.name not in ("analysis.md", f"{incident_id}.md"):
                    try:
                        content_snip = art.read_text(encoding="utf-8", errors="ignore")[
                            :4000
                        ]
                        art_snippets.append(
                            f"### File: {art.name}\n```markdown\n{content_snip}\n```"
                        )
                    except Exception:
                        pass
            if art_snippets:
                artifacts_context = (
                    "\n# ATTACHED DIRECTORY FILES & ARTIFACTS IN THIS TICKET:\n"
                    + "\n\n".join(art_snippets)
                    + "\n"
                )

        user_prompt = f"""# CURRENT STATE CONTRACT (LONG-TERM MEMORY):
{current_content}
{artifacts_context}
{history_str}# LATEST ENGINEER MESSAGE / OBSERVATION:
{user_message}
"""
        if context_attachment:
            user_prompt += f"\n# ATTACHED ERROR LOG OR CODE CONTEXT:\n{context_attachment[:3500]}\n"

        raw_output = LLMBridge.execute(
            user_prompt, model=model, system_prompt=system_prompt
        )

        # Parse the two blocks
        direct_response = ""
        updated_contract = ""

        if "<<<RESPONSE>>>" in raw_output and "<<<CONTRACT>>>" in raw_output:
            parts = raw_output.split("<<<CONTRACT>>>")
            direct_response = parts[0].replace("<<<RESPONSE>>>", "").strip()
            updated_contract = parts[1].strip()
        elif "<<<RESPONSE>>>" in raw_output:
            direct_response = raw_output.replace("<<<RESPONSE>>>", "").strip()
            updated_contract = current_content
        elif "<<<CONTRACT>>>" in raw_output:
            parts = raw_output.split("<<<CONTRACT>>>")
            direct_response = parts[0].strip() or raw_output.strip()
            updated_contract = parts[1].strip()
        else:
            # Model responded directly without delimiters
            direct_response = raw_output.strip()
            updated_contract = current_content

        # Save assistant message to persistent DB history
        self.save_chat_message(
            incident_id, "assistant", direct_response, model_name=model
        )

        # Hygiene check on contract
        required_headers = ["CONFIRMED FACTS", "SPEC & HARD CONSTRAINTS"]
        if any(h in updated_contract for h in required_headers):
            self.save_raw_investigation(incident_id, updated_contract)
            return {"response": direct_response, "updated_doc": updated_contract}
        else:
            # Fallback append if markdown broke
            fallback_entry = f"\n- [{now_str}] Observation: {user_message}"
            safe_doc = current_content + fallback_entry
            self.save_raw_investigation(incident_id, safe_doc)
            return {"response": direct_response, "updated_doc": safe_doc}

    def convert_to_implementation(
        self, incident_id: str, initial_spec: str = ""
    ) -> str:
        """
        Transition an RCA investigation into an Implementation contract.
        Archives the previous RCA contract to rca_pre_implementation.md and writes new implementation contract.
        """
        current_content = self.read_investigation(incident_id)
        ticket_dir = self.get_ticket_dir(incident_id)
        if ticket_dir.exists():
            archive_path = ticket_dir / "rca_pre_implementation.md"
            archive_path.write_text(current_content, encoding="utf-8")

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        base_jira_url = JIRA_URL.rstrip("/")

        # Extract title if possible
        title = incident_id
        for line in current_content.splitlines():
            if line.startswith("# ") and incident_id in line:
                title = (
                    line.replace("# ", "").replace(f"[{incident_id}]", "").strip(" -:")
                )
                break

        specs = f"- [x] RCA Completed (archived in ticket directory)\n- [ ] {initial_spec or 'Implement required fix based on RCA findings'}"
        new_content = IMPLEMENTATION_TEMPLATE.format(
            jira_key=incident_id,
            jira_url=base_jira_url,
            title=title or incident_id,
            status="ACTIVE",
            target_system="Inherited from RCA",
            created_at=now_str,
            last_updated=now_str,
            specs=specs,
            baseline="- (Baseline to be established with initial change)",
            failed_attempts="- (None in implementation phase yet)",
            next_step="- [ ] Define first minimal testable change",
        )
        self.save_raw_investigation(incident_id, new_content)
        return new_content
