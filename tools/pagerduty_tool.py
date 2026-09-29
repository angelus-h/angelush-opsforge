#!/usr/bin/env python3
"""
PagerDuty Incident & Alert Inspection Tool
Fetch active/recent incidents, alert payloads, timeline logs, and synthesize with LLM.

Usage:
  python3 pagerduty_tool.py list --status triggered,acknowledged --limit 20
  python3 pagerduty_tool.py show <incident_id_or_number>
  python3 pagerduty_tool.py alerts <incident_id>
  python3 pagerduty_tool.py analyze <incident_id> "Analyze root cause and suggest Jira fix"

Environment:
  PAGERDUTY_API_KEY (REST API User or General Token)
  PAGERDUTY_API_URL (defaults to https://api.pagerduty.com)
"""

import argparse
import json
import os
import re
import sys
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

# Automatically load .env file if present
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file)
    except ImportError:
        with open(_env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v


def get_pd_config():
    """Retrieve PagerDuty API credentials and base URL."""
    api_key = (
        os.environ.get("PAGERDUTY_API_TOKEN", "").strip()
        or os.environ.get("PAGERDUTY_API_KEY", "").strip()
    )
    base_url = os.environ.get("PAGERDUTY_API_URL", "https://api.pagerduty.com").strip().rstrip("/")
    return api_key, base_url


def pd_request(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute authenticated PagerDuty REST API v2 request."""
    api_key, base_url = get_pd_config()
    if not api_key:
        raise ValueError("PAGERDUTY_API_TOKEN or PAGERDUTY_API_KEY is not set in environment or .env file.")

    url = f"{base_url}{endpoint}"
    if params:
        query_items = []
        for key, value in params.items():
            if isinstance(value, (list, tuple)):
                for item in value:
                    query_items.append((f"{key}[]", str(item)))
            elif value is not None and value != "":
                query_items.append((key, str(value)))
        if query_items:
            url += f"?{urllib.parse.urlencode(query_items)}"

    headers = {
        "Accept": "application/vnd.pagerduty+json;version=2",
        "Authorization": f"Token token={api_key}",
        "Content-Type": "application/json",
        "User-Agent": "SRE-Hub/1.0"
    }

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = resp.read().decode("utf-8", errors="replace")
            return json.loads(data)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"PagerDuty API HTTP {e.code} error: {body}") from e
    except TimeoutError:
        raise RuntimeError(f"Connection to PagerDuty timed out after 45s ({url}). Check your network connection / Red Hat VPN.")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to connect to PagerDuty ({base_url}): {e.reason}") from e


def list_incidents(
    statuses: Optional[List[str]] = None,
    urgency: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 25
) -> List[Dict[str, Any]]:
    """List incidents with given status/filters."""
    if not statuses:
        statuses = ["triggered", "acknowledged"]

    params: Dict[str, Any] = {
        "statuses": statuses,
        "limit": min(limit, 100),
        "sort_by": "created_at:desc"
    }
    if urgency and urgency != "all":
        params["urgencies"] = [urgency]
    if query:
        params["query"] = query

    res = pd_request("/incidents", params=params)
    return res.get("incidents", [])


def get_incident(incident_id_or_number: str) -> Dict[str, Any]:
    """Fetch single incident by ID (e.g. 'PXXXXXX') or incident number (e.g. 1234)."""
    # If numeric, search by query or fetch list matching number
    if incident_id_or_number.isdigit():
        res = pd_request("/incidents", params={"incident_key": incident_id_or_number, "limit": 5})
        incidents = res.get("incidents", [])
        for inc in incidents:
            if str(inc.get("incident_number")) == incident_id_or_number:
                return inc
        # Fallback to query
        res = pd_request("/incidents", params={"query": incident_id_or_number, "limit": 10})
        for inc in res.get("incidents", []):
            if str(inc.get("incident_number")) == incident_id_or_number:
                return inc

    # Otherwise fetch by ID
    res = pd_request(f"/incidents/{incident_id_or_number}")
    return res.get("incident", {})


def get_incident_alerts(incident_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Fetch alert payloads and details associated with an incident."""
    res = pd_request(f"/incidents/{incident_id}/alerts", params={"limit": limit})
    return res.get("alerts", [])


def get_incident_log_entries(incident_id: str, limit: int = 15) -> List[Dict[str, Any]]:
    """Fetch timeline activity log entries for an incident."""
    res = pd_request(f"/incidents/{incident_id}/log_entries", params={"limit": limit})
    return res.get("log_entries", [])


def format_incident_markdown(
    incident: Dict[str, Any],
    alerts: Optional[List[Dict[str, Any]]] = None,
    logs: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Format incident, alerts, and timeline logs into clean Markdown for investigations."""
    inc_num = incident.get("incident_number", "N/A")
    inc_id = incident.get("id", "N/A")
    title = incident.get("title") or incident.get("summary", "Untitled")
    status = incident.get("status", "unknown").upper()
    urgency = incident.get("urgency", "unknown").upper()
    created_at = incident.get("created_at", "N/A")
    html_url = incident.get("html_url", "")
    service = (incident.get("service") or {}).get("summary", "Unspecified Service")
    escalation = (incident.get("escalation_policy") or {}).get("summary", "N/A")

    assignees_list = [
        a.get("assignee", {}).get("summary")
        for a in incident.get("assignments", [])
        if a.get("assignee", {}).get("summary")
    ]
    assignees_str = ", ".join(assignees_list) if assignees_list else "Unassigned"

    lines = [
        f"### PagerDuty Incident #{inc_num}: {title}",
        f"- **ID:** `{inc_id}`",
        f"- **Status:** `{status}` | **Urgency:** `{urgency}`",
        f"- **Service:** `{service}` | **Escalation Policy:** `{escalation}`",
        f"- **Assignee(s):** {assignees_str}",
        f"- **Created:** {created_at}",
    ]
    if html_url:
        lines.append(f"- **PagerDuty Link:** [{html_url}]({html_url})")

    lines.append("")

    # Alerts & raw payloads
    if alerts:
        lines.append("#### Associated Alerts & Payloads:")
        for idx, alert in enumerate(alerts, 1):
            alert_summary = alert.get("summary", "No summary")
            severity = alert.get("severity", "unknown")
            body_details = (alert.get("body") or {}).get("details", {})
            lines.append(f"- **Alert {idx}:** [{severity.upper()}] {alert_summary}")
            if body_details:
                formatted_details = json.dumps(body_details, indent=2, ensure_ascii=False)
                # Keep payload reasonably sized for zero token waste
                if len(formatted_details) > 3000:
                    formatted_details = formatted_details[:3000] + "\n... [TRUNCATED]"
                lines.append("```json\n" + formatted_details + "\n```")
        lines.append("")

    # Timeline log entries
    if logs:
        lines.append("#### Timeline Entries:")
        for log in logs:
            log_type = log.get("type", "log")
            log_time = log.get("created_at", "")
            agent = (log.get("agent") or {}).get("summary", "System")
            summary = log.get("summary") or log_type
            lines.append(f"- `{log_time}` [{agent}] {summary}")
        lines.append("")

    return "\n".join(lines)


def analyze_with_llm(context_text: str, custom_prompt: str = "") -> str:
    """Run LLM analysis on PagerDuty incident text."""
    full_prompt = (
        "You are a Senior SRE performing fast incident triaging and correlation.\n"
        "Here is the PagerDuty incident and alert data:\n\n"
        f"{context_text}\n\n"
    )
    if custom_prompt:
        full_prompt += f"Instruction:\n{custom_prompt}\n\n"
    else:
        full_prompt += (
            "Provide a concise analysis:\n"
            "1. **Core Problem & Symptoms**: What is failing?\n"
            "2. **Impact Scope**: Which service, cluster, host, or tenant is affected?\n"
            "3. **Error Signatures**: Key error codes, metrics, or alerts extracted.\n"
            "4. **Correlated Jira / Investigation Suggestion**: How to name and tag the Jira ticket or incident file.\n"
            "5. **Immediate Diagnostic Actions**: 2-3 specific commands or checks to run.\n"
        )

    for m in ["gemini-3.6-flash", "gemini-flash-lite-latest", "gemini-flash-latest"]:
        try:
            res = subprocess.run(
                ["llm", "prompt", full_prompt, "-m", m],
                capture_output=True,
                text=True,
                check=True
            )
            return res.stdout.strip()
        except Exception:
            continue
    return "LLM execution failed across all candidate models."


def main():
    parser = argparse.ArgumentParser(description="PagerDuty SRE Investigation CLI")
    subparsers = parser.add_subparsers(dest="command")

    # list
    p_list = subparsers.add_parser("list", help="List incidents")
    p_list.add_argument("--status", default="triggered,acknowledged", help="Comma-separated statuses")
    p_list.add_argument("--urgency", default="all", choices=["all", "high", "low"])
    p_list.add_argument("--query", default="", help="Search query")
    p_list.add_argument("--limit", type=int, default=20, help="Result limit")

    # show
    p_show = subparsers.add_parser("show", help="Show incident markdown")
    p_show.add_argument("incident_id", help="Incident ID or Incident Number")

    # alerts
    p_alerts = subparsers.add_parser("alerts", help="Fetch incident alerts")
    p_alerts.add_argument("incident_id", help="Incident ID")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Analyze incident with AI")
    p_analyze.add_argument("incident_id", help="Incident ID or Number")
    p_analyze.add_argument("prompt", nargs="?", default="", help="Custom prompt")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "list":
            statuses = [s.strip() for s in args.status.split(",") if s.strip()]
            incidents = list_incidents(statuses=statuses, urgency=args.urgency, query=args.query, limit=args.limit)
            if not incidents:
                print("No incidents found matching criteria.")
                return
            for inc in incidents:
                status_emoji = "🔴" if inc.get("status") == "triggered" else "🟡" if inc.get("status") == "acknowledged" else "🟢"
                svc = (inc.get("service") or {}).get("summary", "N/A")
                print(f"{status_emoji} #{inc.get('incident_number')} [{inc.get('status').upper()}] {inc.get('title')} ({svc}) - ID: {inc.get('id')}")

        elif args.command == "show":
            inc = get_incident(args.incident_id)
            if not inc:
                sys.exit(f"Incident {args.incident_id} not found.")
            inc_id = inc.get("id")
            alerts = get_incident_alerts(inc_id)
            logs = get_incident_log_entries(inc_id)
            md = format_incident_markdown(inc, alerts=alerts, logs=logs)
            print(md)

        elif args.command == "alerts":
            alerts = get_incident_alerts(args.incident_id)
            print(json.dumps(alerts, indent=2))

        elif args.command == "analyze":
            inc = get_incident(args.incident_id)
            if not inc:
                sys.exit(f"Incident {args.incident_id} not found.")
            inc_id = inc.get("id")
            alerts = get_incident_alerts(inc_id)
            logs = get_incident_log_entries(inc_id)
            md = format_incident_markdown(inc, alerts=alerts, logs=logs)
            print("=" * 60)
            print("INCIDENT CONTEXT")
            print("=" * 60)
            print(md)
            print("=" * 60)
            print("AI TRIAGE & CORRELATION")
            print("=" * 60)
            analysis = analyze_with_llm(md, args.prompt)
            print(analysis)

    except Exception as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
