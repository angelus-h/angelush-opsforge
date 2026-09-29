# 🛠️ AngelusH OpsForge (Stateless & Zero-Waste)

An ultra-efficient, token-optimized SRE cockpit built with Streamlit and Gemini Flash/Flash-Lite models. Designed for Site Reliability Engineers working with **Ansible, OpenShift/Kubernetes, Jenkins, GitLab, Slack, and Jira**.

---

## 🎯 Core Philosophy: Zero-Waste & Stateless

1. **Local Pre-Filtering ($0 Tokens):** Large logs, directory structures, and git diffs are parsed and stripped locally via Python before hitting any LLM API.
2. **Ultra-Low Cost Inference:** Uses Gemini Flash and Gemini Flash-Lite (`ai-flash`, `ai-flash-lite`), reducing routine triage queries to fractions of a cent ($0.001 – $0.005).
3. **Stateless Context Bundling:** Context is isolated in persistent markdown maps (`state/maps/`) and export bundles, preventing bloated chat histories in OpenCode / Claude.

---

## 📂 Architecture & Directory Structure

```text
sre-hub/
├── app.py                      # Main Streamlit dashboard
├── config.py                   # Central settings, auto-repo discovery, paths
├── requirements.txt            # Python dependencies
├── core/
│   ├── llm_bridge.py           # Subprocess wrapper calling local `llm` CLI
│   ├── log_sanitizer.py        # Terminal/Jenkins log stripper & console fetcher
│   ├── map_generator.py        # Local Python AST & Ansible var extractor
│   ├── diff_sanitizer.py       # Noise-filtered git diff parser
│   └── sre_tools.py            # Local wrappers for Slack, Jira, and GitLab CLI tools
├── tools/                      # Self-contained CLI utilities
│   ├── slack_analyze.py        # Slack channel & thread analyzer
│   ├── jira_gemini.py          # Jira search and ticket analyzer
│   └── gitlab_analyze.py       # GitLab MR & pipeline analyzer
└── state/                      # Persistent runtime state (ignored in git)
    ├── maps/                   # Generated architecture maps (<repo_name>.md)
    ├── logs/                   # Sanitized error cores
    ├── digests/                # External tools analysis outputs
    └── bundles/                # OpenCode copy-paste session bundles
```

---

## 🚀 Quickstart & Installation

### 1. Prerequisites & Environment Setup
- Python 3.10+
- The `llm` CLI tool configured with Gemini models (`llm -m gemini-flash-latest`, `llm -m gemini-flash-lite-latest`) or local [Ollama](https://ollama.ai).
- Standard environment variables (configured in `~/.bashrc`, `~/.zshrc`, or local `.env`):
  - `SLACK_WORKSPACE_URL`: Base URL of your Slack instance (e.g. `https://my-team.enterprise.slack.com`).
  - `SLACK_XOXC_TOKEN`, `SLACK_XOXD_TOKEN`: Authenticated user session tokens.
    > *💡 How to obtain Slack tokens:* Extract manually via browser DevTools (see Application tab cookies and localStorage), or utilize a companion extractor utility like `slack-token-extractor` (referenced in `state/maps/slack-token-extractor.md`).
  - `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`: Jira credentials for ticket triage.
  - `GITLAB_URL`, `GITLAB_TOKEN` (or `TF_HTTP_PASSWORD`): GitLab API credentials for MR & pipeline analysis.
  - `REPOS_DIR`: Directory where your repositories reside (defaults to `~/repos`).
  - `INVESTIGATIONS_DIR`: Output directory for investigation notes (defaults to `~/sre-investigations`).
  - `SRE_USER_HANDLE`: Your user handle (e.g. `@username`) to highlight personal mentions in Slack.

### 2. Workspace & Directory Customization
You can explicitly override default directories in two ways:
1. **Environment Variables:**
   ```bash
   # In ~/.bashrc or terminal session:
   export INVESTIGATIONS_DIR="/path/to/my/investigations"
   ```
2. **Streamlit Sidebar UI:**
   - Open the **⚙️ Workspace Configuration** sidebar in the web UI.
   - Enter any custom path into **Investigations Output Directory**. The directory is created automatically if it doesn't exist.

### 2. Automated Quickstart (Recommended)

#### On Linux / macOS (MacBook Pro):
```bash
git clone https://github.com/YOUR_USERNAME/sre-hub.git
cd sre-hub

# One-step install
./install.sh

# Launch the cockpit
./start.sh
```

#### On Windows (PowerShell):
```powershell
cd path\to\sre-hub

# One-step install
.\install.ps1

# Launch the cockpit
.\start.ps1
```

### 3. Manual Virtual Environment Setup (Alternative)
Open your browser at `http://localhost:8501`.

---

## 🎯 Effective Investigation Workflow (The SRE Funnel)

To solve complex SRE incidents and CI/CD pipeline failures while keeping token usage and costs near zero ($0.001 - $0.005 per run), follow the **Funnel Approach**:

```
[ Massive Raw Data (Jenkins, Jira, Slack) ]
                     │
                     ▼ 0 Tokens (Python Local Filtering)
[ Pre-Filtered Core: Failure Window + Architecture Map ]
                     │
                     ▼ Minimal Tokens (~500 - 2k tokens)
[ LLM Synthesis: Root Cause Analysis (Gemini Flash / DeepSeek-R1) ]
                     │
                     ▼ Zero-Bloat Bundle
[ OpenCode CLI: Direct Fix & Verification in Target Repo ]
```

### 1. Step 1: Context Gathering (Jira & Slack)
- **Jira Hub Tab:** Input the ticket key (e.g. `PROJ-1234`). Get a concise digest of reported symptoms and affected components.
- **Slack Analyzer Tab:** Paste the relevant incident thread URL with limit 30–50 messages. Determine if a workaround was already proposed or who is actively working on it.

### 2. Step 2: Extract Skeleton Architecture (Stateless Mapper)
- Instead of dumping thousands of lines of repository code into an LLM, go to the **Repo Mapper** tab.
- Run a **Cold Scan** on the target repository (e.g. your Ansible playbooks or backend repo).
- Generates a dense, ~500-token `AI_ARCHITECTURE_MAP.md` covering playbooks, tasks, roles, and configuration variables.

### 3. Step 3: Strip Noise & Correlate (Log Stripper)
- Fetch or paste the build failure into the **Log & Error Stripper** tab.
- Enable **Strip ANSI & Timestamps** and **Extract Failure Blocks** (extracts `FAILED!`, `fatal:`, tracebacks, and 5xx errors).
- **Attach the Architecture Map** generated in Step 2.
- Run analysis with **Gemini Flash** (cloud) or **DeepSeek-R1:32b** (local). The model cross-references the stripped error with the architecture map to pinpoint the exact task and variable failure.

### 4. Step 4: Model Selection Guidelines
- **Fast Triaging & Summaries:** Use `gemini-flash-latest` (inexpensive, ultra-fast, massive context).
- **Complex RCA & Hidden Logic Flaws:** Use `deepseek-r1:32b` locally (step-by-step chain-of-thought reasoning).
- **Code Generation & Ansible Fixing:** Use `qwen2.5-coder:32b` or `codestral:22b`.

### 5. Step 5: Export to OpenCode CLI
- Open the **OpenCode Context Exporter** tab.
- Click **Save Active Context as Bundle**.
- Switch to your terminal in the target repository and launch OpenCode:
  ```bash
  opencode
  ```
- Instruct OpenCode:
  > *"Review the bundle at `.../state/bundles/bundle_<timestamp>.md` and apply the fix to the identified task."*
- When finished, run `/compact` in OpenCode to keep the session lightweight.

---

## 🖥️ Feature Walkthrough

### 1. 🧭 Active Investigation Tracker (Timeline & Hypotheses)
- **What it does:** Solves context loss and degradation during long-running, multi-day SRE incidents.
- **Strict 4-Section Contract:** Enforces a rigid structure across all investigation files:
  1. `CONFIRMED FACTS` (verified evidence only).
  2. `RULED OUT` (disproven hypotheses so you never waste time re-testing dead ends).
  3. `ACTIVE HYPOTHESES & NEXT EXPERIMENTS` (actionable working theories).
  4. `TIMELINE & EXPERIMENT LOG` (timestamped chronological trace).
- **AI-Driven Updates:** Input your latest findings or experiment results (optionally attaching isolated error logs). The AI engine automatically updates the sections, moves failed theories to *Ruled Out*, and appends the timeline.
- **Direct OpenCode Integration:** 1-click loading into the OpenCode Context Exporter.

### 2. 🗺️ Stateless Repo Mapper
- **What it does:** Scans any Git repository in `$REPOS_DIR` (or custom path).
- **Cold Scan:** Extracts file hierarchy, Python AST classes/functions, and Ansible `defaults/vars` variable names locally with **0 tokens**. Generates a persistent `AI_ARCHITECTURE_MAP.md` saved in `state/maps/<repo_name>.md`.
- **Incremental Diff:** Analyzes recent `git diff` against the saved map to update the architecture without rescanning.

### 3. 🔍 Log & Error Stripper (Jenkins & Terminal)
- **What it does:** Solves the problem of pasting 10,000-line build logs into an AI chat.
- **Direct Paste / Jenkins URL:** Paste terminal output directly, or provide a Jenkins build URL (e.g. `https://jenkins.../job/.../42/`) to automatically download `consoleText`.
- **Intelligent Error Core:** Extracts only lines matching failure keywords (`FATAL:`, `hudson.AbortException`, `exit code [1-9]`, `CrashLoopBackOff`, `FAILED! =>`) with configurable context lines.
- **Correlate with Source Repo:** Attach an architecture map from any repo in `$REPOS_DIR`. The LLM pinpoints the exact playbook, task, or module causing the failure.

### 4. 💬 Slack Analyzer
- Paste a channel ID or thread URL.
- Detects if you are mentioned (`$SRE_USER_HANDLE`) and summarizes what is requested or needed.
- Uses `tools/slack_analyze.py` locally with session tokens.

### 5. 🎫 Jira Hub
- One-click summaries for assigned tickets (`assignee = currentUser()`).
- In-depth root-cause analysis for specific Jira issues (`SPRE-1234`, `KFLUXSPRT-567`).
- Custom JQL query analyzer.

### 6. 🦊 GitLab MR & Pipeline Review
- Review Merge Requests, review comments, and code changes before merging.
- Diagnose failed GitLab CI pipelines by analyzing failed job logs.

### 7. 📋 OpenCode Context Exporter
- Consolidates your active session: active investigation contract, architecture map, sanitized error logs, Slack notes, Jira details, and GitLab summaries into a clean Markdown bundle.
- Save the bundle to `state/bundles/` or copy-paste it into an empty OpenCode session.
- Allows OpenCode to execute tasks with 100% relevant context and zero prior conversation bloat.

---

## 🔒 Security & Privacy
- Sensitive session state files (`state/logs/`, `state/digests/`, `state/bundles/`) and virtual environments are excluded from Git via `.gitignore`.
- Tokens are read strictly from local environment variables; never hardcoded in scripts or configurations.
