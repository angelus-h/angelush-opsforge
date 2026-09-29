#!/usr/bin/env python3
"""
Migrate file-system investigations into SQLite database (Spec 001).
Scans investigations/detailed/, extracts contracts and artifacts, and populates state/investigations.db.
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.db import get_db, init_db


def parse_contract_meta(content: str, folder_name: str):
    """Extracts title, status, and investigation type from contract markdown."""
    title = f"{folder_name} Investigation"
    status = "ACTIVE"
    inv_type = "Implementation"

    # Extract title from first heading
    title_match = re.search(
        r"^#\s+(?:JIRA\s+[A-Za-z]+:\s*)?(?:[A-Za-z0-9]+-\d+\s*[-:]\s*)?([^\n]+)",
        content,
        re.MULTILINE,
    )
    if title_match:
        extracted = title_match.group(1).strip()
        if extracted:
            title = extracted

    # Extract status
    status_match = re.search(
        r"\*\*Status:\*\*\s*([A-Za-z_\-]+)", content, re.IGNORECASE
    )
    if status_match:
        status = status_match.group(1).strip().upper()

    # Extract type
    type_match = re.search(r"\*\*Type:\*\*\s*([A-Za-z_\-]+)", content, re.IGNORECASE)
    if type_match:
        inv_type = type_match.group(1).strip().capitalize()

    return title, status, inv_type


def migrate(storage_dir: Path, dry_run: bool = False):
    print("🚀 Initializing database schema...")
    init_db()

    if not storage_dir.exists():
        print(f"❌ Storage directory does not exist: {storage_dir}")
        return

    print(f"📂 Scanning investigations in: {storage_dir}...")
    migrated_invs = 0
    migrated_artifacts = 0

    with get_db() as conn:
        for item in sorted(storage_dir.iterdir()):
            if item.name.startswith("."):
                continue

            # Skip tool cache/export directories
            if item.name.lower().startswith(("gitlab_mr_", "slack_", "local_")):
                continue

            jira_key = None
            contract_content = None
            updated_at = None

            if item.is_dir():
                is_jira_like = bool(re.match(r"^[A-Za-z0-9]+-\d+", item.name))
                contract_file = item / "analysis.md"

                # Check fallback contract names
                if not contract_file.exists():
                    fallbacks = list(item.glob("*analysis*.md"))
                    if fallbacks:
                        contract_file = fallbacks[0]

                if contract_file.exists():
                    contract_content = contract_file.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    jira_key = item.name.upper()
                    updated_at = datetime.fromtimestamp(
                        contract_file.stat().st_mtime
                    ).strftime("%Y-%m-%d %H:%M:%S")
                elif is_jira_like:
                    # Directory has no analysis.md, check if any md exists
                    md_files = list(item.glob("*.md"))
                    if md_files:
                        contract_content = f"# {item.name}\n\n**Status:** ACTIVE\n**Type:** Investigation\n\nSynthesized from local files."
                        jira_key = item.name.upper()
                        updated_at = datetime.fromtimestamp(
                            item.stat().st_mtime
                        ).strftime("%Y-%m-%d %H:%M:%S")

            elif item.is_file() and item.suffix == ".md":
                is_jira_like = bool(re.match(r"^[A-Za-z0-9]+-\d+", item.name))
                if is_jira_like:
                    jira_key = item.stem.upper()
                    contract_content = item.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    updated_at = datetime.fromtimestamp(item.stat().st_mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )

            if not jira_key or not contract_content:
                continue

            title, status, inv_type = parse_contract_meta(contract_content, jira_key)

            if not dry_run:
                conn.execute(
                    """
                    INSERT INTO investigations (jira_key, title, status, investigation_type, contract_md, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(jira_key) DO UPDATE SET
                        title = excluded.title,
                        status = excluded.status,
                        investigation_type = excluded.investigation_type,
                        contract_md = excluded.contract_md,
                        updated_at = excluded.updated_at
                    """,
                    (jira_key, title, status, inv_type, contract_content, updated_at),
                )

            migrated_invs += 1

            # Migrate artifacts if directory exists
            if item.is_dir():
                for art_file in item.glob("*.md"):
                    if art_file.name in ("analysis.md", "README.md"):
                        continue
                    art_content = art_file.read_text(encoding="utf-8", errors="replace")
                    art_updated = datetime.fromtimestamp(
                        art_file.stat().st_mtime
                    ).strftime("%Y-%m-%d %H:%M:%S")

                    if not dry_run:
                        conn.execute(
                            """
                            INSERT INTO artifacts (jira_key, artifact_name, content, created_at)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(jira_key, artifact_name) DO UPDATE SET
                                content = excluded.content
                            """,
                            (jira_key, art_file.name, art_content, art_updated),
                        )
                    migrated_artifacts += 1

    print("\n✅ Migration complete!")
    print(f"   • Migrated investigations: {migrated_invs}")
    print(f"   • Migrated artifacts:     {migrated_artifacts}")


if __name__ == "__main__":
    storage = Path(
        os.getenv(
            "INVESTIGATIONS_DIR",
            os.path.expanduser("~/ai/konflux-lumino/investigations/detailed"),
        )
    )
    migrate(storage)
