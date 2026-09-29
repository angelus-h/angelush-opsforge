#!/usr/bin/env python3
"""
ServiceNow Integration & Request Drafter Tool for SRE-Hub (Spec 005).
Connects to Red Hat ServiceNow (REST API), verifies Change Requests & Incidents,
and automatically drafts Red Hat standard CHG / Service Requests from Jira contracts.
"""

import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from core.db import get_db, init_db
from core.llm_bridge import LLMBridge

# ServiceNow State Mappings
CHG_STATES = {
    "-5": "New",
    "1": "New",
    "2": "Assess",
    "3": "Authorize",
    "4": "Scheduled",
    "5": "Implement (Active Window)",
    "6": "Review",
    "7": "Closed",
    "8": "Canceled"
}

INC_STATES = {
    "1": "New",
    "2": "In Progress",
    "3": "On Hold",
    "6": "Resolved",
    "7": "Closed",
    "8": "Canceled"
}


def get_snow_headers() -> Dict[str, str]:
    user = os.getenv("SERVICENOW_USERNAME")
    pwd = os.getenv("SERVICENOW_PASSWORD")
    if not user or not pwd:
        raise ValueError("SERVICENOW_USERNAME or SERVICENOW_PASSWORD not configured.")
    auth = "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return {
        "Authorization": auth,
        "Accept": "application/json",
        "Content-Type": "application/json"
    }


def query_snow_record(table: str, number_or_sys_id: str) -> Optional[Dict]:
    """Queries a single ServiceNow record by number (e.g. CHG0123456, INC0987654) or sys_id."""
    instance_url = os.getenv("SERVICENOW_INSTANCE_URL", "https://redhathub.service-now.com").rstrip("/")
    headers = get_snow_headers()
    target = number_or_sys_id.strip().upper()

    params = {
        "sysparm_query": f"number={target}^ORsys_id={target}",
        "sysparm_limit": 1
    }
    url = f"{instance_url}/api/now/table/{table}?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
            results = data.get("result", [])
            return results[0] if results else None
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")[:250]
        print(f"ServiceNow API error {e.code}: {err}")
        return None
    except Exception as e:
        print(f"Failed to query ServiceNow: {e}")
        return None


def get_record_auto(number: str) -> Optional[Dict]:
    """Automatically detects table type from record number prefix (CHG, INC, RITM, REQ)."""
    clean_num = number.strip().upper()
    if clean_num.startswith("CHG"):
        rec = query_snow_record("change_request", clean_num)
        if rec:
            rec["_record_type"] = "Change Request"
            rec["_state_label"] = CHG_STATES.get(str(rec.get("state")), f"State {rec.get('state')}")
        return rec
    elif clean_num.startswith("INC"):
        rec = query_snow_record("incident", clean_num)
        if rec:
            rec["_record_type"] = "Incident"
            rec["_state_label"] = INC_STATES.get(str(rec.get("state")), f"State {rec.get('state')}")
        return rec
    elif clean_num.startswith("RITM"):
        rec = query_snow_record("sc_req_item", clean_num)
        if rec:
            rec["_record_type"] = "Requested Item"
        return rec
    elif clean_num.startswith("REQ"):
        rec = query_snow_record("sc_request", clean_num)
        if rec:
            rec["_record_type"] = "Service Request"
        return rec
    else:
        # Default try change_request then incident
        rec = query_snow_record("change_request", clean_num) or query_snow_record("incident", clean_num)
        return rec


def draft_snow_change_from_jira(jira_key: str, request_type: str = "Standard Change") -> str:
    """
    Synthesizes a structured ServiceNow Change Request or Firewall/Access Request draft
    from the Jira investigation contract using Gemini.
    """
    init_db()
    safe_key = jira_key.strip().upper()
    contract_md = ""

    with get_db() as conn:
        row = conn.execute("SELECT contract_md, title FROM investigations WHERE jira_key = ?", (safe_key,)).fetchone()
        if row:
            contract_md = row["contract_md"]
            jira_title = row["title"]
        else:
            raise FileNotFoundError(f"Investigation {safe_key} not found in database.")

    system_prompt = (
        "You are an SRE Technical Lead preparing a production-ready ServiceNow RFC (Request for Change) or Access Request.\n"
        "Generate a structured, professional submission matching enterprise Red Hat ServiceNow requirements.\n"
        "Extract all details directly from the provided investigation contract.\n"
        "Provide clear, copy-pasteable fields."
    )

    user_prompt = f"""Generate a ServiceNow {request_type} form draft for Jira ticket: {safe_key} ({jira_title})

# SOURCE JIRA INVESTIGATION CONTRACT:
{contract_md}

OUTPUT STRUCTURE MUST BE EXACTLY AS FOLLOWS (Markdown format with codeblocks):

# 🎫 ServiceNow Draft: {request_type} ({safe_key})

### 1. Header Information
- **Short Description**: (Max 80 chars, e.g. [{safe_key}] Implement Datadog agent proxy config)
- **Change Type / Category**: {request_type}
- **Configuration Item (CI)**: (Identified target repository or service)
- **Risk & Impact**: (Low / Moderate / High, with 1-sentence justification)

### 2. Business Justification & Change Purpose
(Why is this change necessary? What problem does it solve?)

### 3. Implementation & Execution Plan
(Numbered, step-by-step commands, playbooks, or PR merges to run during the window)
```bash
# Exact execution commands
```

### 4. Pre & Post-Change Verification Plan (Test Plan)
(How do we verify service health and that the change succeeded?)
```bash
# Verification commands
```

### 5. Backout & Rollback Plan
(Exact steps to revert if failure occurs, ensuring zero production disruption)
```bash
# Rollback commands
```

### 6. Communication & Outage Window
- **User / Service Impact**: (None / Intermittent / Downtime)
- **Estimated Duration**: (e.g. 30 minutes)
"""

    draft_result = LLMBridge.execute(user_prompt, model="gemini-3.6-flash", system_prompt=system_prompt)

    # Save to artifacts
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    artifact_filename = f"servicenow_draft_{timestamp}.md"

    from core.investigation_tracker import InvestigationTracker
    tracker = InvestigationTracker()
    tracker.save_artifact(safe_key, artifact_filename, draft_result)

    return draft_result


def link_snow_record(jira_key: str, snow_number: str) -> Dict:
    """Fetches ServiceNow record and links it to Jira in SQLite ticket_relations."""
    init_db()
    safe_jira = jira_key.strip().upper()
    safe_snow = snow_number.strip().upper()

    rec = get_record_auto(safe_snow)
    if not rec:
        raise ValueError(f"Could not find ServiceNow record '{safe_snow}'. Check permissions or number.")

    summary = rec.get("short_description", "")
    state_label = rec.get("_state_label", str(rec.get("state", "")))
    rec_type = rec.get("_record_type", "ServiceNow").lower().replace(" ", "_")

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO ticket_relations (source_key, target_key, relation_type, summary, status)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_key, target_key, relation_type) DO UPDATE SET
                summary = excluded.summary,
                status = excluded.status
            """,
            (safe_jira, safe_snow, f"has_snow_{rec_type}", summary, state_label)
        )

    return {
        "number": safe_snow,
        "type": rec.get("_record_type", "ServiceNow"),
        "summary": summary,
        "state": state_label
    }


def import_snow_record_to_investigation(jira_key: str, snow_number: str) -> Dict:
    """
    Fetches the full details of a ServiceNow ticket (CHG, INC, RITM)
    and imports it directly into the investigation as an artifact and relation.
    """
    init_db()
    safe_jira = jira_key.strip().upper()
    safe_snow = snow_number.strip().upper()

    rec = get_record_auto(safe_snow)
    if not rec:
        raise ValueError(f"Could not find ServiceNow record '{safe_snow}'. Check permissions or number.")

    rec_type = rec.get("_record_type", "Record")
    state_label = rec.get("_state_label", str(rec.get("state", "")))
    short_desc = rec.get("short_description", "No description")
    desc = rec.get("description", "(No detailed description)")
    justification = rec.get("justification", "(No justification provided)")
    impl_plan = rec.get("implementation_plan", "(No implementation plan)")
    test_plan = rec.get("test_plan", "(No test plan)")
    backout_plan = rec.get("backout_plan", "(No backout plan)")
    close_notes = rec.get("close_notes", "(None)")
    created = rec.get("sys_created_on", "")
    updated = rec.get("sys_updated_on", "")
    start_d = rec.get("start_date", "Unscheduled")
    end_d = rec.get("end_date", "Unscheduled")

    # Format into markdown artifact
    md = f"""# 🎫 ServiceNow {rec_type}: {safe_snow}
**Summary:** {short_desc}  
**State:** {state_label} (Code: {rec.get('state')})  
**Created:** {created} | **Updated:** {updated}  
**Maintenance Window:** {start_d} -> {end_d}  

## 1. Description & Scope
{desc}

## 2. Business Justification
{justification}

## 3. Implementation Plan
```
{impl_plan}
```

## 4. Test & Verification Plan
```
{test_plan}
```

## 5. Backout & Rollback Plan
```
{backout_plan}
```

## 6. Closure / Resolution Notes
{close_notes}
"""

    from core.investigation_tracker import InvestigationTracker
    tracker = InvestigationTracker()
    artifact_name = f"servicenow_{safe_snow}.md"
    tracker.save_artifact(safe_jira, artifact_name, md)

    # Link in relations
    tracker.add_ticket_relation(
        source_key=safe_jira,
        target_key=safe_snow,
        relation_type=f"has_snow_{rec_type.lower().replace(' ', '_')}",
        summary=short_desc,
        status=state_label
    )

    return {
        "number": safe_snow,
        "type": rec_type,
        "summary": short_desc,
        "state": state_label,
        "markdown": md,
        "artifact_file": artifact_name
    }


def analyze_vulnerability_ticket(jira_key: str) -> str:
    """
    Parses a Qualys/ServiceNow vulnerability Jira ticket (like SPRE-6677),
    extracts the VUL group, compliance deadline, VIT ticket count, and
    generates a comprehensive SRE diagnostic and CVE discovery guide.
    """
    import re
    safe_key = jira_key.strip().upper()

    from tools.jira_relations import get_jira_auth_headers
    base_url = os.getenv("JIRA_URL", "https://redhat.atlassian.net").rstrip("/")
    headers = get_jira_auth_headers()
    endpoint = f"{base_url}/rest/api/3/issue/{safe_key}?fields=summary,status,description"

    req = urllib.request.Request(endpoint, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)

    fields = data.get("fields", {})
    summary = fields.get("summary", "")

    def extract_ast_text(node):
        if not isinstance(node, dict):
            return ""
        text = node.get("text", "") if node.get("type") == "text" else ""
        for child in node.get("content", []):
            text += extract_ast_text(child)
        if node.get("type") in ("paragraph", "tableRow", "heading"):
            text += "\n"
        return text

    desc_text = extract_ast_text(fields.get("description", {}))

    vul_match = re.search(r"\b(VUL\d+)\b", summary + " " + desc_text)
    vul_id = vul_match.group(1) if vul_match else "VUL0136299"

    date_match = re.search(r"Targeted Compliance Date:\s*([0-9\-:\s]+)", desc_text)
    target_date = date_match.group(1).strip() if date_match else "2026-11-14"

    vits = re.findall(r"\b(VIT\d+)\b", desc_text)
    vit_count = len(set(vits))

    sys_match = re.search(r"for\s+([A-Za-z0-9_\-]+)\s*-\s*([A-Za-z0-9_\-]+)", summary)
    system_name = f"{sys_match.group(1)} - {sys_match.group(2)}" if sys_match else "DGIT-001 (dist-git)"

    system_prompt = (
        "You are a Senior Principal SRE and Security Operations Lead analyzing an enterprise Qualys Vulnerability Patching assignment.\n"
        "Explain clearly what this vulnerability ticket means, the risk of missing the compliance deadline, "
        "how the ServiceNow Qualys lifecycle operates, and provide exact copy-paste diagnostic commands for the SRE to run on the target host to see which CVEs/packages need updating."
    )

    prompt = f"""Analyze Qualys Vulnerability Remediation for Jira Ticket: {safe_key}
- **Summary**: {summary}
- **Vulnerability Group**: {vul_id}
- **Compliance Target Date**: {target_date}
- **Open Vulnerability Items (VITs)**: {vit_count} open items
- **Target System**: {system_name}

Provide a structured, actionable SRE investigation guide:
1. **Executive Summary & Scope**: What is {vul_id} on {system_name}, why did InfoSec flag it, and what happens if missed?
2. **Qualys & ServiceNow Lifecycle**: How Qualys automated scanning works, why Jira must not be manually closed early, and why an approved ServiceNow Change Request (CHG) is mandatory before touching production.
3. **Host-Level CVE Discovery Commands**: Exact copy-paste commands to inspect pending CVEs on the host (using dnf updateinfo, check-update, etc.).
4. **Dist-Git Patching Execution Steps**: Using standard playbooks (~/playbooks/dgit-patching/patch_dgit.yml):
   - Dry-run (--check)
   - Preprod verification
   - Production execution (with reboot option)
5. **Post-Patch Verification**: How to confirm the system is clean and ready for Qualys rescan.
"""

    analysis = LLMBridge.execute(prompt, model="gemini-3.6-flash", system_prompt=system_prompt)

    # Save to artifacts
    from core.investigation_tracker import InvestigationTracker
    tracker = InvestigationTracker()
    art_name = f"vulnerability_analysis_{vul_id}.md"
    tracker.save_artifact(safe_key, art_name, analysis)

    return analysis


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 tools/servicenow_tool.py check <CHG/INC_NUMBER>")
        print("  python3 tools/servicenow_tool.py draft <JIRA_KEY> [Change Type]")
        print("  python3 tools/servicenow_tool.py link <JIRA_KEY> <CHG/INC_NUMBER>")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "check":
        num = sys.argv[2]
        rec = get_record_auto(num)
        if rec:
            print(f"✅ Found {rec.get('_record_type', 'Record')} {rec.get('number')}:")
            print(f"   • Summary: {rec.get('short_description')}")
            print(f"   • State:   {rec.get('_state_label', rec.get('state'))}")
            print(f"   • Priority:{rec.get('priority')}")
            print(f"   • Updated: {rec.get('sys_updated_on')}")
        else:
            print(f"❌ Record {num} not found.")

    elif cmd == "draft":
        key = sys.argv[2]
        chg_type = sys.argv[3] if len(sys.argv) > 3 else "Standard Change"
        print(f"📝 Drafting ServiceNow {chg_type} from {key}...")
        draft = draft_snow_change_from_jira(key, request_type=chg_type)
        print("\n" + draft)

    elif cmd == "link":
        key = sys.argv[2]
        num = sys.argv[3]
        res = link_snow_record(key, num)
        print(f"✅ Linked {key} -> {res['number']} ({res['type']}): {res['summary']} [{res['state']}]")
