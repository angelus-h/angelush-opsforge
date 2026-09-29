# Spec 004: Tekton & Konflux CI/CD Pipeline Analyzer (`tools/tekton_tool.py`)

**Goal:** Analyze Konflux and OpenShift Pipelines (Tekton) PipelineRuns and TaskRuns, extracting only the specific failed step (`step-...`) from ten-thousand-line CI/CD logs to minimize LLM token usage.

**Status:** Draft
**Date:** 2026-09-24
**Version:** 1.0.0

---

## 1. Motivation & Konflux Background

In Konflux systems, `PipelineRun` objects execute dozens of `TaskRun`s (build-container, clonerefs, fips-check, clamav-scan, etc.).
When a build fails:
- The output of `tkn pipelinerun logs <name>` is often **15,000 – 40,000 lines**.
- Feeding this into an LLM costs dollars and can easily cause token limits to be exceeded.
- **The reality:** The failure is always caused by a single `step` within a single `TaskRun` (`exitCode != 0`).

---

## 2. CLI and Python Interface

### CLI Commands:
```bash
# Quickly detect a failed PipelineRun and extract the failed step's log
python3 tools/tekton_tool.py pipelinerun <pr_name> --namespace <ns> [--jira <JIRA_KEY>]

# Inspect steps of a specific TaskRun
python3 tools/tekton_tool.py taskrun <tr_name> --namespace <ns>

# List recently failed PipelineRuns in a namespace
python3 tools/tekton_tool.py list-failed --namespace <ns>
```

### Analysis Process:
1. Fetch `kubectl / oc get pipelinerun <name> -n <ns> -o json`.
2. Iterate through `status.childReferences`: which `TaskRun` has status `Succeeded == False`.
3. From the failed TaskRun's status (`status.steps`), select the failed container / step:
   - `terminated.exitCode != 0`
   - `terminated.reason: Error`
4. Fetch only the specific pod/container log (`oc logs <pod> -c step-<name>`).
5. Use `core/log_sanitizer.py` to extract the clean error core and pass it to the Gemini model.

---

## 3. UI Integration and Hybrid Save

- If a Jira ticket is provided (`--jira SPRE-6318`), the `tekton_pipeline_failure.md` is automatically saved into the ticket's artifacts and SQLite database.
- In the Streamlit investigation UI, the failed step's output immediately appears as a context attachment.
