# Spec 003: OpenShift Diagnostics Tool (`tools/openshift_tool.py`)

**Goal:** Fast, token-efficient OpenShift/Kubernetes diagnostic tool that fetches and masks logs of failing pods, crashed containers, and warning events, making them directly attachable to the Jira investigation contract.

**Status:** Draft
**Date:** 2026-09-24
**Version:** 1.0.0

---

## 1. Motivation & SRE Use Cases

- **Common problems:** `CrashLoopBackOff`, `OOMKilled`, `ImagePullBackOff`, `CreateContainerConfigError`.
- **Preventing token waste:** A full `oc describe pod` or a multi-thousand line pod log cannot be passed directly to the LLM.
- **The solution:**
  1. Local Python pre-filtering: extract only the last 50-100 lines of crashed containers (`oc logs -c <container> --tail=100 -p`).
  2. Automatic masking of sensitive data (tokens, passwords, cert keys) via `core/log_sanitizer.py`.
  3. One-click save as an artifact to the investigation (`save_artifact(jira_key, "openshift_pod_error.log", ...)`) or direct attachment to the co-pilot chat.

---

## 2. CLI and Python Interface

### CLI Commands:
```bash
# List failing pods in a namespace
python3 tools/openshift_tool.py pods --namespace <ns> [--failing-only]

# Targeted extraction of a specific failing pod
python3 tools/openshift_tool.py diagnose <pod-name> --namespace <ns> [--jira <JIRA_KEY>]

# Fetch Warning Events from the last 30 minutes
python3 tools/openshift_tool.py events --namespace <ns>
```

### Python API (`tools/openshift_tool.py`):
```python
def get_failing_pods(namespace: str) -> List[Dict]:
    """Returns pods with status != Running/Completed or restartCount > 0."""

def extract_pod_failure_core(pod_name: str, namespace: str, tail_lines: int = 100) -> Dict:
    """
    Extracts exit code, termination reason, and last sanitized log lines
    from the crashing container.
    """

def get_recent_warning_events(namespace: str, minutes: int = 30) -> List[Dict]:
    """Returns deduplicated warning/failed events."""
```

---

## 3. UI Integration (Streamlit)

- New tab or embedded module in the left sidebar / expander:
  - Namespace and Pod selector.
  - "🩺 Diagnose Pod Failure" button: immediately displays the root cause (`OOMKilled`, `exitCode: 137`) and the clean log extract.
  - "📎 Attach to Investigation": inserts it into the current ticket's artifacts.
