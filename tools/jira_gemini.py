#!/usr/bin/env python3
"""
Jira + Gemini Integration
Fetch Jira tickets + analyze with Gemini Flash (ultra-cheap).

Usage:
  python3 jira_gemini.py summarize           # my tickets (default)
  python3 jira_gemini.py analyze SPRE-1234   # single ticket analysis
  python3 jira_gemini.py search "<JQL>"      # search + bulk analysis

Environment:
  JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN (Atlassian Cloud)
"""

import argparse
import base64
import json
import os
import sys
import subprocess
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

# Jira config
JIRA_API_PATH = "/rest/api/3/search/jql"
JIRA_FIELDS = "summary,status,description"


def _env(name):
    """Get required env var or exit."""
    val = os.environ.get(name)
    if not val:
        sys.exit(f"error: {name} not set")
    return val


def search_jql(base_url, auth_header, jql, max_results=100):
    """Fetch Jira issues by JQL query."""
    issues = []
    next_token = None
    while True:
        params = {"jql": jql, "maxResults": max_results, "fields": JIRA_FIELDS}
        if next_token:
            params["nextPageToken"] = next_token
        url = f"{base_url}{JIRA_API_PATH}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": auth_header,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            sys.exit(
                f"error: Jira HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
            )
        except urllib.error.URLError as e:
            sys.exit(f"error: cannot reach Jira ({e.reason})")

        for issue in data.get("issues", []):
            f = issue.get("fields", {})
            issues.append(
                {
                    "key": issue.get("key"),
                    "summary": f.get("summary"),
                    "status": (f.get("status") or {}).get("name"),
                    "description": f.get("description", ""),
                }
            )
        next_token = data.get("nextPageToken")
        if data.get("isLast", True) or not next_token:
            break
    return issues


def bucket(issue):
    """Categorize issue by status."""
    status = (issue.get("status") or "").lower()
    if "progress" in status:
        return "in_progress"
    if "review" in status:
        return "in_review"
    return "other"


def fetch_my_tickets():
    """Fetch user's assigned tickets grouped by status."""
    base_url = _env("JIRA_URL").rstrip("/")
    email = _env("JIRA_EMAIL")
    token = _env("JIRA_API_TOKEN")
    auth_header = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()

    open_jql = (
        "assignee = currentUser() AND status not in (Closed, Done, Resolved) "
        "ORDER BY updated DESC"
    )
    result = {"in_progress": [], "in_review": [], "other": []}
    for issue in search_jql(base_url, auth_header, open_jql):
        result[bucket(issue)].append(issue)

    return result


def analyze_with_llm(prompt):
    """Analyze using Gemini Flash via llm CLI with automated fallbacks."""
    for m in ["gemini-3.6-flash", "gemini-flash-lite-latest", "gemini-flash-latest"]:
        try:
            result = subprocess.run(
                ["llm", "prompt", prompt, "-m", m],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout
        except subprocess.CalledProcessError:
            continue
        except FileNotFoundError:
            sys.exit("error: llm CLI not found (install via: pip install llm)")
    sys.exit("error: llm analysis failed across all candidate models")


def cmd_summarize(args):
    """Summarize my assigned tickets."""
    tickets = fetch_my_tickets()

    print("=" * 60)
    print("MY JIRA TICKETS")
    print("=" * 60)

    total = 0
    for section in ["in_progress", "in_review", "other"]:
        issues = tickets.get(section, [])
        if not issues:
            continue
        total += len(issues)
        print(f"\n{section.upper().replace('_', ' ')} ({len(issues)}):")
        for issue in issues:
            print(f"  {issue['key']} — {issue['summary']}")

    if total == 0:
        print("\nNo assigned tickets.")
        return

    # Analyze with Gemini
    lines = []
    for section, issues in tickets.items():
        if issues:
            for issue in issues:
                lines.append(f"[{issue['key']}] {issue['summary']} ({issue['status']})")

    context = f"Analyze my {total} current Jira tickets:\n\n" + "\n".join(lines)
    prompt = (
        context
        + """

Provide concise analysis:
1. **Priorities**: What should I focus on first?
2. **Blockers**: Anything I should unblock immediately?
3. **Effort**: Rough estimate for In Progress items
4. **Recommendations**: Next steps or dependencies to watch

Keep it under 300 words."""
    )

    print("\n" + "=" * 60)
    print("GEMINI ANALYSIS")
    print("=" * 60 + "\n")
    analysis = analyze_with_llm(prompt)
    print(analysis)
    print("=" * 60)


def cmd_search(args):
    """Search Jira by JQL + analyze results."""
    jql = args.jql
    print(f"🔍 Searching: {jql}\n")

    base_url = _env("JIRA_URL").rstrip("/")
    email = _env("JIRA_EMAIL")
    token = _env("JIRA_API_TOKEN")
    auth_header = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()

    issues = search_jql(base_url, auth_header, jql, max_results=50)
    if not issues:
        print("No issues found.")
        return

    print(f"✅ Found {len(issues)} issues\n")
    for issue in issues:
        print(f"{issue['key']} — {issue['summary']} ({issue['status']})")

    # Analyze with Gemini
    lines = [f"[{i['key']}] {i['summary']} ({i['status']})" for i in issues]
    context = f"Analyze these {len(lines)} Jira search results:\n\n" + "\n".join(lines)
    prompt = (
        context
        + """

Provide analysis:
1. **Common Themes**: What patterns do you see?
2. **Status Distribution**: Status breakdown
3. **Workload**: Who has the most work?
4. **Recommendations**: What to prioritize?

Keep it concise."""
    )

    print("\n" + "=" * 60)
    print("ANALYSIS")
    print("=" * 60 + "\n")
    analysis = analyze_with_llm(prompt)
    print(analysis)
    print("=" * 60)


def cmd_analyze(args):
    """Analyze single ticket with detailed investigation."""
    issue_key = args.issue_key

    base_url = _env("JIRA_URL").rstrip("/")
    email = _env("JIRA_EMAIL")
    token = _env("JIRA_API_TOKEN")
    auth_header = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()

    print(f"🔍 Fetching {issue_key}...\n")
    issues = search_jql(base_url, auth_header, f"key = {issue_key}")
    if not issues:
        print(f"Issue {issue_key} not found.")
        return

    issue = issues[0]
    print(f"{issue['key']} — {issue['summary']}")
    print(f"Status: {issue['status']}")
    if issue["description"]:
        print(f"Description: {issue['description']}\n")

    # Check for existing investigation directory
    default_inv = os.getenv(
        "INVESTIGATIONS_DIR",
        os.path.expanduser("~/ai/konflux-lumino/investigations/detailed"),
    )
    inv_base = Path(default_inv)
    inv_dir = inv_base / issue_key

    if inv_dir.exists():
        print(f"📁 Found existing investigation: {inv_dir}")
        # Read previous findings if any
        findings_file = inv_dir / "analysis.md"
        if findings_file.exists():
            print("   Reading previous analysis...\n")
    else:
        print(f"📁 Creating new investigation: {inv_dir}")
        inv_dir.mkdir(parents=True, exist_ok=True)

    # Detailed analysis with Gemini
    context = f"Provide detailed investigation for Jira ticket:\n\n[{issue['key']}] {issue['summary']}\nStatus: {issue['status']}"
    if issue["description"]:
        context += f"\nDescription: {issue['description']}"

    prompt = (
        context
        + """

Provide comprehensive analysis with these sections:

## Executive Summary
Brief 1-2 sentence overview.

## Problem Analysis
- What is the core issue?
- Why does it matter?
- Impact scope (who/what is affected)?

## Technical Breakdown
- Root cause (if identifiable)
- Related components or systems
- Known constraints or limitations

## Approach & Options
- Recommended solution
- Alternative approaches
- Pros/cons of each

## Implementation Details
- Key steps or phases
- Potential blockers
- Dependencies to resolve

## Effort & Timeline
- Realistic time estimate
- Risk assessment
- Success criteria

## Next Steps
- Immediate action items
- Follow-up investigations needed
- Stakeholders to involve

Be detailed but concise. Use markdown formatting."""
    )

    print("=" * 60)
    print("DETAILED ANALYSIS")
    print("=" * 60 + "\n")
    analysis = analyze_with_llm(prompt)
    print(analysis)
    print("=" * 60)

    # Save analysis to file
    analysis_file = inv_dir / "analysis.md"
    with open(analysis_file, "w") as f:
        f.write(f"# {issue_key}: {issue['summary']}\n\n")
        f.write(f"**Status**: {issue['status']}\n")
        f.write(
            f"**Generated**: {subprocess.run(['date', '-u', '+%Y-%m-%d %H:%M:%S'], capture_output=True, text=True).stdout.strip()}\n\n"
        )
        f.write(analysis)

    print(f"\n✅ Analysis saved to: {analysis_file}")


def main():
    ap = argparse.ArgumentParser(description="Jira + Gemini analyzer", add_help=True)
    subparsers = ap.add_subparsers(dest="command", help="command")

    # summarize
    subparsers.add_parser("summarize", help="my assigned tickets + analysis")

    # analyze
    ap_analyze = subparsers.add_parser("analyze", help="analyze single ticket")
    ap_analyze.add_argument("issue_key", help="ticket key (e.g., SPRE-1234)")

    # search
    ap_search = subparsers.add_parser("search", help="search by JQL + analysis")
    ap_search.add_argument("jql", help="JQL query")

    args = ap.parse_args()

    # Show help if no command
    if not args.command:
        ap.print_help()
        return

    if args.command == "summarize":
        cmd_summarize(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "search":
        cmd_search(args)


if __name__ == "__main__":
    main()
