# SRE-Hub Development Roadmap & Spec-Driven Development Framework

**Version:** 1.0.0  
**Project:** SRE Operations Hub (`/home/mgreczi/ai/konflux-lumino/projects/sre-hub`)  
**Status:** Approved / Active Execution

---

## 🧭 Development Phases & Specifications

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. DATA LAYER & ENGINE (Phase 1)                                            │
│ Spec 001: SQLite Investigation Store, FTS5 & Hybrid Disk Sync               │
│ └── Fast queries, persistent chat history, zero fragility                   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ 2. RELATIONSHIP NETWORK (Phase 2)                                           │
│ Spec 002: Jira Relations & Dependency Graph                                 │
│ └── Blockers (blocks/is blocked by), parent-child tickets, visual graph     │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ 3. INFRA & PIPELINE TOOLS (Phase 3 & 4)                                     │
│ Spec 003: OpenShift Tool (`tools/openshift_tool.py`)                        │
│ └── Pod failures, CrashLoopBackOff, Warning events gathering & masking      │
│ Spec 004: Tekton & Konflux Tool (`tools/tekton_tool.py`)                     │
│ └── PipelineRun and TaskRun step-level failure extraction (zero waste log)  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ 4. ENTERPRISE PROCESSES (Phase 5)                                            │
│ Spec 005: ServiceNow Tool (`tools/servicenow_tool.py`)                      │
│ └── Change Request (CHG) window check & Incident (INC) synchronization      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 📋 Specifications Status

| Spec Number | Title | Status | Priority |
| :--- | :--- | :--- | :--- |
| **`001`** | **SQLite Investigation Store & Hybrid Engine** | **Spec Ready, Implementable** | 🔴 P0 (Immediate) |
| **`002`** | **Jira Relations & Dependency Graph** | Draft | 🟠 P1 |
| **`003`** | **OpenShift Diagnostic Tool (`oc`)** | Draft | 🟡 P2 |
| **`004`** | **Tekton / Konflux Pipeline Analyzer (`tkn`)** | Draft | 🟡 P2 |
| **`005`** | **ServiceNow Change & Incident Manager (SNOW)** | Draft | 🟢 P3 |

---

## 🛠️ Spec-Driven Workflow Rules

1. **No feature is implemented without specification:** Before any new tool or module, a `specs/00X-*.md` document must be created with exact APIs, inputs, outputs, and token filtering rules.
2. **Review & Approval:** The specification must be approved by the user before coding begins.
3. **Self-Verification & Validation:** A standalone test or validation script must be created for every component to prove completion.
