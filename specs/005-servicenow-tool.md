# Spec 005: ServiceNow Tool (`tools/servicenow_tool.py`)

**Goal:** ServiceNow (SNOW) REST API integration for Change Management (CHG) and Incident Management (INC) processes, enabling maintenance window verification and customer communication generation.

**Status:** Draft  
**Date:** 2026-09-24  
**Version:** 1.0.0  

---

## 1. SRE Use Cases

1. **Change Request (CHG) Check (Pre-Flight Check):**
   * Before an SRE intervenes in production systems, it must be verified:
     - Is the CHG approved (`State: Scheduled` or `Implement`).
     - Does the current time fall within the permitted maintenance window (`start_date <= now <= end_date`).
2. **Incident (INC) Linking & Communication:**
   * During an incident investigation, linking the customer-reported INC number with the internal Jira (`SPRE-*`, `KFLUXSPRT-*`).
   * Generating sanitized, professional status updates (Work Notes / Customer Comments) using the LLM.

---

## 2. Authentication & Configuration

Environment variables (`.env`):
```bash
SNOW_INSTANCE_URL=https://redhat.service-now.com
SNOW_USERNAME=...
SNOW_PASSWORD=...
# Or OAuth / API Token:
SNOW_TOKEN=...
```

---

## 3. CLI and Python Interface

```bash
# Check CHG status and maintenance window
python3 tools/servicenow_tool.py check-chg CHG0123456

# Fetch and summarize an incident
python3 tools/servicenow_tool.py get-inc INC0987654 [--jira <JIRA_KEY>]

# Generate a communication draft based on the current investigation state
python3 tools/servicenow_tool.py draft-update INC0987654 --jira SPRE-6318
```
