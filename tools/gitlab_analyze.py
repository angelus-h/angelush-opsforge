#!/usr/bin/env python3
"""
GitLab & Local Repository Analysis with Gemini Flash

USAGE:

  # Analyze GitLab MR (review comments + changes)
  python3 gitlab_analyze.py "https://gitlab.cee.redhat.com/ansible-roles/postgresql/-/merge_requests/102"

  # Analyze GitLab Pipeline (if failed, shows error logs)
  python3 gitlab_analyze.py "https://gitlab.cee.redhat.com/ansible-roles/postgresql/-/pipelines/17268823"

  # Analyze local repository with your question
  python3 gitlab_analyze.py ~/repos/postgresql "Explain this Ansible role to me"

  # Analyze specific file with your question
  python3 gitlab_analyze.py ~/repos/postgresql/defaults/main.yml "What does this file do?"

FEATURES:

  GitLab Analysis:
  - MR: Review comments, file changes, Gemini recommendations
  - Pipeline: Failed job logs, error diagnosis, suggested fixes

  Local Analysis:
  - Repo: Recent commits, file structure, custom analysis
  - File: Full content review, explanation, clarification
  - Saves analysis to ~/ai/konflux-lumino/investigations/detailed/

ENVIRONMENT:

  GITLAB_URL        (default: https://gitlab.cee.redhat.com)
  GITLAB_TOKEN      (or use TF_HTTP_PASSWORD from ~/.bashrc)

EXAMPLES:

  # Review an MR before merging
  python3 gitlab_analyze.py "https://gitlab.cee.redhat.com/ansible-roles/postgresql/-/merge_requests/102"

  # Debug a failed pipeline
  python3 gitlab_analyze.py "https://gitlab.cee.redhat.com/ansible-roles/postgresql/-/pipelines/12345678"

  # Understand an Ansible role
  python3 gitlab_analyze.py ~/repos/postgresql "What is this role's purpose and how is it structured?"

  # Learn about a specific file
  python3 gitlab_analyze.py ~/repos/postgresql/templates/postgresql.conf-13.j2 "Explain what this template does"

  # Code review question
  python3 gitlab_analyze.py ~/repos/postgresql/defaults/main.yml "What changed in PostgreSQL configuration?"
"""

import argparse
import json
import os
import re
import sys
import subprocess
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# Auto-load .env from project root if present
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    try:
        with open(_env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k = _k.strip()
                    _v = _v.strip().strip("'\"")
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v
    except Exception:
        pass


def _env(name, default=None):
    """Get env var or use default."""
    val = os.environ.get(name)
    if not val and not default:
        sys.exit(f"error: {name} not set")
    return val or default


def get_gitlab_headers():
    """Build GitLab API auth headers."""
    token = _env("GITLAB_TOKEN") or _env("GITLAB_API_TOKEN") or _env("TF_HTTP_PASSWORD")
    if not token:
        sys.exit("error: GITLAB_TOKEN or TF_HTTP_PASSWORD not set")
    return {"PRIVATE-TOKEN": token}


def parse_gitlab_url(url):
    """Extract base_url, repo, type, and ID from GitLab URL."""
    # Matches any GitLab host: https://<gitlab-host>/group/subgroup/repo/-/merge_requests/102
    mr_match = re.search(r"(https?://[^/]+)/(.+?)/-/merge_requests/(\d+)", url)
    if mr_match:
        return mr_match.group(1), mr_match.group(2), "mr", int(mr_match.group(3))

    # Matches any GitLab host: https://<gitlab-host>/group/subgroup/repo/-/pipelines/17268823
    pipeline_match = re.search(r"(https?://[^/]+)/(.+?)/-/pipelines/(\d+)", url)
    if pipeline_match:
        return (
            pipeline_match.group(1),
            pipeline_match.group(2),
            "pipeline",
            int(pipeline_match.group(3)),
        )

    sys.exit(f"error: invalid GitLab URL format: {url}")


def gitlab_api_call(path, headers, base_url=None):
    """Make GitLab API request."""
    if not base_url:
        base_url = _env("GITLAB_URL", "https://gitlab.com").rstrip("/")
    url = f"{base_url}/api/v4{path}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_detail = e.read().decode(errors="replace")[:300]
        sys.exit(f"error: GitLab API HTTP {e.code}: {error_detail}")
    except urllib.error.URLError as e:
        reason_str = str(e.reason)
        if (
            "Name or service not known" in reason_str
            or "getaddrinfo failed" in reason_str
        ):
            sys.exit(
                f"error: cannot reach GitLab at '{base_url}' ({reason_str}).\n"
                f"Hint: If this is an internal Red Hat GitLab server (e.g. gitlab.cee.redhat.com), "
                f"please ensure your Red Hat Global VPN connection is active (e.g. nmcli con up '1 - Red Hat Global VPN')."
            )
        sys.exit(f"error: cannot reach GitLab ({base_url}): {e.reason}")


def analyze_with_llm(prompt):
    """Analyze using Gemini Flash via llm CLI with automated fallbacks."""
    candidate_models = [
        "gemini-3.6-flash",
        "gemini-flash-lite-latest",
        "gemini-flash-latest",
    ]
    last_err = ""
    for m in candidate_models:
        try:
            result = subprocess.run(
                ["llm", "prompt", prompt, "-m", m],
                capture_output=True,
                text=True,
                check=True,
            )
            out = result.stdout.strip()
            if out:
                return out
        except subprocess.CalledProcessError as e:
            last_err = e.stderr or e.stdout
            continue
        except FileNotFoundError:
            sys.exit("error: llm CLI not found (install via: pip install llm)")

    return f"⚠️ LLM analysis temporarily unavailable ({last_err.strip()}). Review the raw comments and diffs above."


def cmd_analyze_mr(repo, mr_number, base_url=None):
    """Analyze MR: review + diffs + Gemini."""
    print(f"🔍 Analyzing MR !{mr_number} in {repo}...\n")

    headers = get_gitlab_headers()
    project_id = urllib.parse.quote(repo, safe="")

    # Fetch MR data
    mr_data = gitlab_api_call(
        f"/projects/{project_id}/merge_requests/{mr_number}", headers, base_url=base_url
    )
    mr_title = mr_data.get("title")
    mr_status = mr_data.get("state")

    print(f"Title: {mr_title}")
    print(f"Status: {mr_status}\n")

    # Fetch discussions (comments/reviews)
    discussions = gitlab_api_call(
        f"/projects/{project_id}/merge_requests/{mr_number}/discussions",
        headers,
        base_url=base_url,
    )

    # Extract review comments
    review_comments = []
    for discussion in discussions:
        for note in discussion.get("notes", []):
            if note.get("body") and not note.get("system"):
                reviewer = note.get("author", {}).get("username", "Unknown")
                body = note.get("body")
                review_comments.append(f"@{reviewer}: {body}")

    # Fetch changes (diffs)
    changes = gitlab_api_call(
        f"/projects/{project_id}/merge_requests/{mr_number}/changes",
        headers,
        base_url=base_url,
    )

    files_changed = []
    for change in changes.get("changes", []):
        new_path = change.get("new_path")
        old_path = change.get("old_path")
        diff = change.get("diff", "")[:1000]  # First 1000 chars
        files_changed.append({"path": new_path or old_path, "diff_snippet": diff})

    # Build context for Gemini
    context = f"GitLab MR Analysis\n\n## MR Details\n- **Title**: {mr_title}\n- **Status**: {mr_status}\n- **Number**: !{mr_number}\n\n"

    if review_comments:
        context += "## Review Comments\n"
        for comment in review_comments[:5]:  # Limit to first 5
            context += f"- {comment}\n"
        context += "\n"

    if files_changed:
        context += "## Changed Files\n"
        for fc in files_changed[:5]:  # First 5 files
            context += f"### {fc['path']}\n```\n{fc['diff_snippet']}\n```\n"

    prompt = (
        context
        + """

Provide analysis:
1. **Summary**: What does this MR do?
2. **Review Feedback**: What are reviewers asking for?
3. **Code Changes**: Are the changes appropriate?
4. **Action Items**: What needs to be fixed or addressed?
5. **Approval Status**: Is this ready to merge or are there blockers?

Be concise and actionable."""
    )

    print("=" * 60)
    print("ANALYSIS")
    print("=" * 60 + "\n")

    analysis = analyze_with_llm(prompt)
    print(analysis)
    print("=" * 60)

    # Save
    default_inv = os.getenv(
        "INVESTIGATIONS_DIR",
        os.path.expanduser("~/ai/konflux-lumino/investigations/detailed"),
    )
    inv_base = Path(default_inv)
    inv_dir = inv_base / f"gitlab_mr_{mr_number}"
    inv_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    analysis_file = inv_dir / f"mr_analysis_{timestamp}.md"

    with open(analysis_file, "w") as f:
        f.write(f"# MR !{mr_number}: {mr_title}\n\n")
        f.write(f"**Repo**: {repo}\n")
        f.write(f"**Status**: {mr_status}\n")
        f.write(
            f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        )
        if review_comments:
            f.write("## Review Comments\n")
            for comment in review_comments:
                f.write(f"- {comment}\n")
            f.write("\n")
        f.write("## Analysis\n\n")
        f.write(analysis)

    print(f"\n✅ Analysis saved to: {analysis_file}")

    # If the MR references a Jira ticket (e.g. SPRE-6318), also sync directly to that investigation directory
    jira_match = re.search(r"([A-Za-z]+-\d+)", mr_title)
    if jira_match:
        jira_ticket = jira_match.group(1).upper()
        jira_inv_dir = inv_base / jira_ticket
        if jira_inv_dir.is_dir():
            jira_review_file = jira_inv_dir / "gitlab_review.md"
            with open(jira_review_file, "w") as jf:
                jf.write(f"# GitLab MR !{mr_number} Review ({jira_ticket})\n\n")
                jf.write(f"**MR Title**: {mr_title}\n")
                jf.write(f"**Status**: {mr_status}\n\n")
                if review_comments:
                    jf.write("## Review Comments\n")
                    for comment in review_comments:
                        jf.write(f"- {comment}\n")
                    jf.write("\n")
                jf.write("## Analysis\n\n")
                jf.write(analysis)
            print(f"✅ Also synced directly to Jira investigation: {jira_review_file}")


def cmd_analyze_pipeline(repo, pipeline_id, base_url=None):
    """Analyze pipeline: job status + logs if failed."""
    print(f"🔍 Analyzing pipeline {pipeline_id} in {repo}...\n")

    headers = get_gitlab_headers()
    project_id = urllib.parse.quote(repo, safe="")

    # Fetch pipeline
    pipeline_data = gitlab_api_call(
        f"/projects/{project_id}/pipelines/{pipeline_id}", headers, base_url=base_url
    )
    status = pipeline_data.get("status", "unknown")

    print(f"Status: {status}")
    print(f"Web: {pipeline_data.get('web_url')}\n")

    # Fetch jobs
    jobs = gitlab_api_call(
        f"/projects/{project_id}/pipelines/{pipeline_id}/jobs",
        headers,
        base_url=base_url,
    )

    passed = [j for j in jobs if j.get("status") == "success"]
    failed = [j for j in jobs if j.get("status") in ["failed", "error"]]

    print(f"Jobs: {len(passed)} passed, {len(failed)} failed/error\n")

    if not failed:
        print("✅ All jobs passed!")
        return

    # Analyze failed jobs
    print("Fetching failed job logs...\n")

    job_analyses = []
    for job in failed[:3]:
        job_name = job.get("name")
        job_id = job.get("id")

        try:
            log_text = gitlab_api_call(
                f"/projects/{project_id}/jobs/{job_id}/trace",
                headers,
                base_url=base_url,
            )
            log_snippet = log_text[-3000:] if len(log_text) > 3000 else log_text
        except Exception as e:
            log_snippet = f"[Log fetch failed: {e}]"

        job_analyses.append(
            {"name": job_name, "status": job.get("status"), "log": log_snippet}
        )

    # Gemini analysis
    analysis_prompt = (
        f"Analyze these CI/CD pipeline failures (pipeline {pipeline_id}):\n\n"
    )
    for ja in job_analyses:
        analysis_prompt += (
            f"**{ja['name']}** ({ja['status']}):\n```\n{ja['log'][-2000:]}\n```\n\n"
        )

    analysis_prompt += """Provide:
1. **Root Cause**: What's failing?
2. **Error Type**: Compilation, dependency, timeout, auth, etc?
3. **Quick Fix**: How to resolve?
4. **Prevention**: How to avoid this?

Be concise and actionable."""

    print("=" * 60)
    print("ANALYSIS")
    print("=" * 60 + "\n")

    analysis = analyze_with_llm(analysis_prompt)
    print(analysis)
    print("=" * 60)

    # Save
    default_inv = os.getenv(
        "INVESTIGATIONS_DIR",
        os.path.expanduser("~/ai/konflux-lumino/investigations/detailed"),
    )
    inv_base = Path(default_inv)
    inv_dir = inv_base / f"gitlab_pipeline_{pipeline_id}"
    inv_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    analysis_file = inv_dir / f"pipeline_analysis_{timestamp}.md"

    with open(analysis_file, "w") as f:
        f.write(f"# Pipeline {pipeline_id} Analysis\n\n")
        f.write(f"**Repo**: {repo}\n")
        f.write(f"**Status**: {status}\n")
        f.write(f"**Failed Jobs**: {len(failed)}\n")
        f.write(
            f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        )
        f.write("## Failed Job Details\n\n")
        for ja in job_analyses:
            f.write(
                f"### {ja['name']} ({ja['status']})\n```\n{ja['log'][-2000:]}\n```\n\n"
            )
        f.write("## Analysis\n\n")
        f.write(analysis)

    print(f"\n✅ Analysis saved to: {analysis_file}")


def is_url(s):
    """Check if string is a URL."""
    return s.startswith("http://") or s.startswith("https://")


def cmd_analyze_local(path_str, prompt):
    """Analyze local file or repo."""
    path = Path(path_str).expanduser()

    if not path.exists():
        sys.exit(f"error: path does not exist: {path}")

    print(f"📂 Analyzing: {path}\n")

    context = ""

    # Check if it's a git repo
    if (path / ".git").exists():
        print("   Git repo detected. Gathering context...\n")

        # Recent commits
        try:
            result = subprocess.run(
                ["git", "-C", str(path), "log", "--oneline", "-10"],
                capture_output=True,
                text=True,
                check=True,
            )
            recent_commits = result.stdout
        except:
            recent_commits = "[Could not fetch commits]"

        # File listing
        try:
            result = subprocess.run(
                [
                    "find",
                    str(path),
                    "-type",
                    "f",
                    "-not",
                    "-path",
                    "*/.git/*",
                    "-not",
                    "-path",
                    "*/.*",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            files = result.stdout.strip().split("\n")[:20]  # First 20 files
            file_list = "\n".join(files)
        except:
            file_list = "[Could not list files]"

        context = f"""## Repository: {path}

### Recent Commits
```
{recent_commits}
```

### File Structure (first 20 files)
```
{file_list}
```

### Your Question
{prompt}"""

    else:
        # Single file
        print("   Reading file...\n")

        try:
            with open(path, "r") as f:
                file_content = f.read()
            # Limit to first 5000 chars
            if len(file_content) > 5000:
                file_content = file_content[:5000] + "\n\n[... truncated ...]"
        except Exception as e:
            sys.exit(f"error: cannot read file: {e}")

        context = f"""## File: {path}

### Content
```
{file_content}
```

### Your Question
{prompt}"""

    # Gemini analysis
    analysis_prompt = (
        context
        + """

Provide a clear, helpful explanation addressing the user's question.
Use markdown formatting. Be specific and reference code when relevant."""
    )

    print("=" * 60)
    print("ANALYSIS")
    print("=" * 60 + "\n")

    analysis = analyze_with_llm(analysis_prompt)
    print(analysis)
    print("=" * 60)

    # Save
    default_inv = os.getenv(
        "INVESTIGATIONS_DIR",
        os.path.expanduser("~/ai/konflux-lumino/investigations/detailed"),
    )
    inv_base = Path(default_inv)
    repo_name = path.name or "repo"
    inv_dir = inv_base / f"local_{repo_name}"
    inv_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    analysis_file = inv_dir / f"analysis_{timestamp}.md"

    with open(analysis_file, "w") as f:
        f.write(f"# Local Analysis: {path}\n\n")
        f.write(f"**Path**: {path}\n")
        f.write(f"**Question**: {prompt}\n")
        f.write(
            f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        )
        f.write(analysis)

    print(f"\n✅ Analysis saved to: {analysis_file}")


def main():
    ap = argparse.ArgumentParser(
        description="GitLab & Local Analysis with Gemini",
        usage="%(prog)s <url-or-path> [question]",
    )
    ap.add_argument("target", help="GitLab URL or local path (~/repos/...)")
    ap.add_argument(
        "question", nargs="?", help="Question about local repo/file (if not a URL)"
    )

    args = ap.parse_args()

    try:
        if is_url(args.target):
            # GitLab URL
            base_url, repo, obj_type, obj_id = parse_gitlab_url(args.target)
            print(f"\n{'='*60}")
            print(f"GitLab {obj_type.upper()} Analysis: {base_url}/{repo}")
            print(f"{'='*60 + chr(10)}")

            if obj_type == "mr":
                cmd_analyze_mr(repo, obj_id, base_url=base_url)
            elif obj_type == "pipeline":
                cmd_analyze_pipeline(repo, obj_id, base_url=base_url)
        else:
            # Local path
            if not args.question:
                ap.error("question is required for local path analysis")
            print(f"\n{'='*60}")
            print("Local Analysis")
            print(f"{'='*60 + chr(10)}")
            cmd_analyze_local(args.target, args.question)

    except KeyboardInterrupt:
        print("\n⏹️  Cancelled.")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
