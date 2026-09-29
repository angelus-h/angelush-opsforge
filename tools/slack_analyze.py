#!/usr/bin/env python3
"""
Slack Channel & Thread Analysis with Gemini
Analyze channel history, threads, or search + custom prompt.

Usage:
  python3 slack_analyze.py C0123456789 "Summarize channel contents" --limit 50
  python3 slack_analyze.py "https://slack.com/archives/C0123456789/p1787571869858709?thread_ts=1787571869.858709" "Summarize thread"
  python3 slack_analyze.py "https://slack.com/archives/C0123456789" "Search for blockers" --search "incident"

Environment:
  SLACK_XOXC_TOKEN, SLACK_XOXD_TOKEN, SLACK_WORKSPACE_URL
"""

import os
import sys
import subprocess
import re
import json
import urllib.request
import urllib.error
import urllib.parse
import argparse
from datetime import datetime, timezone
from pathlib import Path


def extract_channel_and_thread(input_str):
    """Extract channel ID and optional thread TS from URL or direct ID."""
    thread_ts = None

    # Direct channel/DM ID: C0BHFL9FJTG, D0BCC1N6Q0G
    if re.match(r"^[CD][A-Z0-9]+$", input_str):
        return input_str, None

    # URL with thread: https://redhat-internal.slack.com/archives/C06RG4T1Z0A/p1787571869858709?thread_ts=1787571869.858709
    match = re.search(r"/archives/([CD\w]+)/p(\d+)", input_str)
    if match:
        channel_id = match.group(1)
        # Try to extract thread_ts from query param
        thread_match = re.search(r"thread_ts=([0-9.]+)", input_str)
        if thread_match:
            thread_ts = thread_match.group(1)
        else:
            # Fallback: convert p1787571869858709 → 1787571869.858709
            ts_str = match.group(2)
            thread_ts = f"{ts_str[:10]}.{ts_str[10:]}"
        return channel_id, thread_ts

    # URL without thread (channel only): https://redhat.enterprise.slack.com/archives/C0BHFL9FJTG
    match = re.search(r"/archives/([CD\w]+)", input_str)
    if match:
        return match.group(1), None

    sys.exit(f"error: invalid channel ID or URL: {input_str}")


def fetch_channel_history(channel_id, thread_ts=None, limit=100, search_query=None):
    """Fetch messages from channel or thread using Slack API."""
    xoxc = os.environ.get("SLACK_XOXC_TOKEN")
    xoxd = os.environ.get("SLACK_XOXD_TOKEN")
    workspace_url = os.environ.get("SLACK_WORKSPACE_URL", "https://slack.com").rstrip(
        "/"
    )

    if not xoxc or not xoxd:
        sys.exit("error: SLACK_XOXC_TOKEN and SLACK_XOXD_TOKEN not set")

    if thread_ts:
        # Use conversations.replies for thread messages
        url = f"{workspace_url}/api/conversations.replies"
        params = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": limit,
        }
        print(f"   Fetching {limit} messages from thread in #{channel_id}")
    elif search_query:
        # Use search.messages for keyword filtering
        url = f"{workspace_url}/api/search.messages"
        params = {
            "query": f"in:{channel_id} {search_query}",
            "count": limit,
            "sort": "timestamp",
        }
        print(f"   Searching in #{channel_id} for: '{search_query}'")
    else:
        # Use conversations.history for channel history
        url = f"{workspace_url}/api/conversations.history"
        params = {
            "channel": channel_id,
            "limit": limit,
        }
        print(f"   Fetching {limit} messages from #{channel_id}")

    url_with_params = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url_with_params,
        headers={
            "Authorization": f"Bearer {xoxc}",
        },
    )
    req.add_header("Cookie", f"d={xoxd}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_detail = e.read().decode(errors="replace")[:300]
        sys.exit(f"error: Slack API HTTP {e.code}: {error_detail}")
    except urllib.error.URLError as e:
        sys.exit(f"error: cannot reach Slack ({e.reason})")

    if not data.get("ok"):
        sys.exit(f"error: Slack API error: {data.get('error', 'unknown')}")

    # Handle both search.messages and conversations.history responses
    if search_query:
        messages = [m["message"] for m in data.get("messages", [])]
    else:
        messages = data.get("messages", [])

    return messages


def format_messages(messages):
    """Format messages for Gemini analysis."""
    lines = []
    for msg in messages:
        ts = float(msg.get("ts", 0))
        time_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        user = msg.get("user", "Unknown")
        text = msg.get("text", "")

        # Strip Slack formatting (basic cleanup)
        text = text.replace("<@", "@").replace(">", "")
        text = text.replace("<#", "#").replace("|", " ")
        text = text.replace("<!", "").replace("!", "")

        # Skip empty or system messages
        if not text.strip() or text.startswith("_"):
            continue

        lines.append(f"[{time_str}] @{user}: {text}")

    return "\n".join(lines)


def analyze_with_llm(context, prompt):
    """Analyze using Gemini Flash via llm CLI."""
    detailed_instructions = """
Provide comprehensive analysis with these sections:

## Executive Summary
Brief overview (2-3 sentences).

## Key Points
- Main topics/themes identified
- Important discussions or decisions
- Notable patterns or trends

## Action Items
- Any explicit action items mentioned
- Decisions made or pending

## Recommendations
- Suggested next steps
- Areas needing attention
- Potential blockers or risks

## Additional Context
- Relevant connections to other topics
- Background or historical context if applicable

Be detailed and structured. Use markdown formatting."""

    full_prompt = f"{context}\n\n{prompt}\n\n{detailed_instructions}"

    for m in ["gemini-3.6-flash", "gemini-flash-lite-latest", "gemini-flash-latest"]:
        try:
            result = subprocess.run(
                ["llm", "prompt", full_prompt, "-m", m],
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


def main():
    ap = argparse.ArgumentParser(
        description="Analyze Slack channel history with Gemini",
        usage="%(prog)s CHANNEL PROMPT [--limit N] [--search QUERY]",
    )
    ap.add_argument("channel", help="channel ID (C0BHFL9FJTG) or URL")
    ap.add_argument("prompt", help="custom analysis prompt")
    ap.add_argument(
        "--limit", type=int, default=100, help="max messages to fetch (default: 100)"
    )
    ap.add_argument("--search", type=str, help="search filter (optional)")

    args = ap.parse_args()

    print("🔍 Analyzing Slack...\n")
    channel_id, thread_ts = extract_channel_and_thread(args.channel)

    messages = fetch_channel_history(
        channel_id, thread_ts=thread_ts, limit=args.limit, search_query=args.search
    )
    print(f"   Found {len(messages)} messages\n")

    if not messages:
        print("No messages found.")
        return

    # Format messages
    formatted = format_messages(messages)

    if thread_ts:
        context = (
            f"Slack thread in #{channel_id} ({len(messages)} messages):\n\n{formatted}"
        )
    elif args.search:
        context = f"Slack channel #{channel_id} messages matching '{args.search}' ({len(messages)} messages):\n\n{formatted}"
    else:
        context = f"Slack channel #{channel_id} history (latest {len(messages)} messages):\n\n{formatted}"

    print("=" * 60)
    print("ANALYSIS")
    print("=" * 60 + "\n")

    analysis = analyze_with_llm(context, args.prompt)
    print(analysis)
    print("\n" + "=" * 60)

    # Save analysis to file
    default_inv = os.getenv(
        "INVESTIGATIONS_DIR",
        os.path.expanduser("~/ai/konflux-lumino/investigations/detailed"),
    )
    inv_base = Path(default_inv)
    if thread_ts:
        inv_dir = inv_base / f"slack_{channel_id}_thread_{thread_ts.replace('.', '_')}"
    else:
        inv_dir = inv_base / f"slack_{channel_id}"
    inv_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    analysis_file = inv_dir / f"analysis_{timestamp}.md"

    with open(analysis_file, "w") as f:
        f.write(f"# Slack Analysis — #{channel_id}\n\n")
        if thread_ts:
            f.write("**Type:** Thread\n")
            f.write(f"**Thread TS:** {thread_ts}\n")
        else:
            f.write("**Type:** Channel\n")
        f.write(f"**Prompt:** {args.prompt}\n")
        if args.search:
            f.write(f"**Search filter:** {args.search}\n")
        f.write(f"**Messages analyzed:** {len(messages)}\n")
        f.write(
            f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        )
        f.write(analysis)

    print(f"\n✅ Analysis saved to: {analysis_file}")


if __name__ == "__main__":
    main()
