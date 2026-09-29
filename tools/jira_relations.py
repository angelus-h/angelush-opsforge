#!/usr/bin/env python3
"""
Jira Relations & Dependency Sync Tool (Spec 002).
Fetches issuelinks, parent/epic, and subtasks from Jira REST API,
and synchronizes them into SQLite ticket_relations table.
"""

import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from core.db import get_db, init_db


def get_jira_auth_headers() -> Dict[str, str]:
    email = os.getenv("JIRA_EMAIL")
    token = os.getenv("JIRA_API_TOKEN")
    if not email or not token:
        raise ValueError("JIRA_EMAIL or JIRA_API_TOKEN environment variables not set.")
    auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
    return {"Authorization": auth, "Accept": "application/json"}


def fetch_jira_issue_relations(jira_key: str) -> List[Dict[str, str]]:
    """
    Fetches an issue from Jira API and extracts all outward/inward links,
    parent links, and subtasks.
    """
    base_url = os.getenv("JIRA_URL", "https://redhat.atlassian.net").rstrip("/")
    headers = get_jira_auth_headers()
    safe_key = jira_key.strip().upper()
    endpoint = f"{base_url}/rest/api/3/issue/{safe_key}?fields=summary,status,issuelinks,parent,subtasks"

    req = urllib.request.Request(endpoint, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode(errors="replace")[:200]
        print(f"Jira API HTTP error {e.code}: {err_msg}")
        return []
    except Exception as e:
        print(f"Failed to fetch Jira issue {safe_key}: {e}")
        return []

    fields = data.get("fields", {})
    relations = []

    # 1. Issue Links
    for link in fields.get("issuelinks", []):
        link_type = link.get("type", {})
        if "inwardIssue" in link:
            target = link["inwardIssue"]
            t_key = target.get("key", "").upper()
            t_status = target.get("fields", {}).get("status", {}).get("name", "Unknown")
            t_summary = target.get("fields", {}).get("summary", "")
            rel_name = link_type.get("inward", "relates_to").lower().replace(" ", "_")
            relations.append(
                {
                    "source_key": safe_key,
                    "target_key": t_key,
                    "relation_type": rel_name,
                    "summary": t_summary,
                    "status": t_status,
                }
            )

        if "outwardIssue" in link:
            target = link["outwardIssue"]
            t_key = target.get("key", "").upper()
            t_status = target.get("fields", {}).get("status", {}).get("name", "Unknown")
            t_summary = target.get("fields", {}).get("summary", "")
            rel_name = link_type.get("outward", "relates_to").lower().replace(" ", "_")
            relations.append(
                {
                    "source_key": safe_key,
                    "target_key": t_key,
                    "relation_type": rel_name,
                    "summary": t_summary,
                    "status": t_status,
                }
            )

    # 2. Parent / Epic link
    parent = fields.get("parent")
    if parent:
        p_key = parent.get("key", "").upper()
        p_status = parent.get("fields", {}).get("status", {}).get("name", "Unknown")
        p_summary = parent.get("fields", {}).get("summary", "")
        relations.append(
            {
                "source_key": safe_key,
                "target_key": p_key,
                "relation_type": "child_of",
                "summary": p_summary,
                "status": p_status,
            }
        )

    # 3. Subtasks
    for sub in fields.get("subtasks", []):
        s_key = sub.get("key", "").upper()
        s_status = sub.get("fields", {}).get("status", {}).get("name", "Unknown")
        s_summary = sub.get("fields", {}).get("summary", "")
        relations.append(
            {
                "source_key": safe_key,
                "target_key": s_key,
                "relation_type": "has_subtask",
                "summary": s_summary,
                "status": s_status,
            }
        )

    return relations


def sync_jira_relations(jira_key: str) -> List[Dict[str, str]]:
    """
    Fetches relations from Jira and updates SQLite ticket_relations table.
    """
    init_db()
    safe_key = jira_key.strip().upper()
    relations = fetch_jira_issue_relations(safe_key)

    if not relations:
        return []

    with get_db() as conn:
        for rel in relations:
            conn.execute(
                """
                INSERT INTO ticket_relations (source_key, target_key, relation_type, summary, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_key, target_key, relation_type) DO UPDATE SET
                    summary = excluded.summary,
                    status = excluded.status
                """,
                (
                    rel["source_key"],
                    rel["target_key"],
                    rel["relation_type"],
                    rel["summary"],
                    rel["status"],
                ),
            )

    return relations


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 tools/jira_relations.py <JIRA_KEY>")
        sys.exit(1)

    key = sys.argv[1].strip().upper()
    print(f"🔄 Syncing Jira relations for {key}...")
    rels = sync_jira_relations(key)
    print(f"✅ Found and synced {len(rels)} relations:")
    for r in rels:
        print(
            f"   • [{r['relation_type']}] -> {r['target_key']} ({r['status']}): {r['summary']}"
        )
