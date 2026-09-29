import os
import re
from datetime import datetime
from pathlib import Path
import streamlit as st

from config import (
    DEFAULT_REPOS,
    DEFAULT_REPOS_DIR,
    DEFAULT_INVESTIGATIONS_DIR,
    MAPS_DIR,
    BUNDLES_DIR,
    SRE_USER_HANDLE,
    SLACK_WORKSPACE_URL,
    GITLAB_URL,
)
from core.map_generator import RepoScanner
from core.diff_sanitizer import DiffSanitizer
from core.log_sanitizer import LogSanitizer
from core.llm_bridge import LLMBridge
from core.sre_tools import SREExternalTools
from core.investigation_tracker import InvestigationTracker

APP_DIR = Path(__file__).resolve().parent
LOGO_PATH = APP_DIR / "assets" / "logo.png"

st.set_page_config(
    page_title="AngelusH SRE Hub",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "🛠️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize session state namespaces
for key in [
    "repo_payload",
    "generated_map",
    "sanitized_error",
    "triage_result",
    "slack_result",
    "jira_result",
    "jira_summarize_result",
    "gitlab_result",
    "pagerduty_result",
]:
    if key not in st.session_state:
        st.session_state[key] = ""

# Sidebar: Global Workspace Settings
with st.sidebar:
    if LOGO_PATH.exists():
        col_sb_l, col_sb_c, col_sb_r = st.columns([1, 6, 1])
        with col_sb_c:
            st.image(str(LOGO_PATH), use_container_width=True)
    st.header("⚙️ Workspace Configuration")
    custom_inv_dir = st.text_input(
        "Investigations Output Directory",
        value=os.getenv("INVESTIGATIONS_DIR", str(DEFAULT_INVESTIGATIONS_DIR)),
        help="Target folder where tools (Jira, Slack, GitLab) save investigation markdown files.",
    )
    if custom_inv_dir:
        os.environ["INVESTIGATIONS_DIR"] = custom_inv_dir
        Path(custom_inv_dir).mkdir(parents=True, exist_ok=True)
        st.caption(f"📁 Active output: `{custom_inv_dir}`")

    st.divider()
    st.subheader("🤖 Global LLM / Model Engine")
    available_models = LLMBridge.list_available_models()
    default_model_idx = 0
    if LLMBridge.MODEL_FLASH in available_models:
        default_model_idx = available_models.index(LLMBridge.MODEL_FLASH)

    selected_global_model = st.selectbox(
        "Active Model (Cloud / Ollama Local)",
        available_models,
        index=default_model_idx,
        help="Select any model configured in 'llm' CLI or Ollama (e.g. qwen2.5-coder, deepseek-r1).",
    )
    st.caption(f"⚡ Current Engine: `{selected_global_model}`")

tracker = InvestigationTracker(storage_dir=Path(custom_inv_dir))


def render_export_to_tracker(
    tracker_inst: InvestigationTracker,
    content_to_export: str,
    default_summary: str,
    observation_text: str,
    key_prefix: str,
    selected_model: str,
    system_name: str = "Infrastructure",
    artifact_filename: Optional[str] = None,
):
    """Reusable component providing a consistent 'Export to Investigation Tracker' interface across all tools."""
    st.markdown("---")
    with st.expander("🎯 Export to Investigation Tracker (Jira-Centric)", expanded=True):
        st.caption(
            "Consolidate tool outputs and artifacts into a dedicated folder per Jira ticket in `investigations/detailed/<TICKET>/`."
        )
        existing_invs = tracker_inst.list_investigations()

        c_mode, c_key = st.columns([1, 1])
        with c_mode:
            target_mode = st.radio(
                "Target Ticket",
                ["Select Existing Investigation", "Enter Jira Issue Key"]
                if existing_invs
                else ["Enter Jira Issue Key"],
                key=f"{key_prefix}_target_mode",
                horizontal=True,
            )

        with c_key:
            if target_mode == "Select Existing Investigation" and existing_invs:
                def_idx = 0
                active_id = st.session_state.get("active_investigation_id")
                if active_id in existing_invs:
                    def_idx = existing_invs.index(active_id)
                target_jira_key = st.selectbox(
                    "Active Investigation",
                    existing_invs,
                    index=def_idx,
                    key=f"{key_prefix}_select_key",
                )
            else:
                target_jira_key = (
                    st.text_input(
                        "Jira Issue Key",
                        value=st.session_state.get("active_investigation_id", ""),
                        placeholder="e.g. SPRE-5115 or KFLUXSPRT-1234",
                        key=f"{key_prefix}_input_key",
                    )
                    .strip()
                    .upper()
                )

        if st.button(
            "📤 Export to Investigation Tracker",
            key=f"{key_prefix}_export_btn",
            type="primary",
            use_container_width=True,
        ):
            if not target_jira_key:
                st.warning("Please provide a valid Jira Issue Key (e.g. SPRE-5115).")
            else:
                art_name = artifact_filename
                if not art_name:
                    art_map = {
                        "mapper": "AI_ARCHITECTURE_MAP.md",
                        "triage": "rca_triage.md",
                        "triage_raw": "sanitized_error.log",
                        "slack": "slack_findings.md",
                        "jira": "jira_digest.md",
                        "gitlab": "gitlab_review.md",
                        "pd": "pagerduty_incident.md",
                    }
                    art_name = art_map.get(key_prefix, f"{key_prefix}_artifact.md")

                # Save raw artifact in the dedicated ticket directory
                tracker_inst.save_artifact(target_jira_key, art_name, content_to_export)

                if not tracker_inst.exists(target_jira_key):
                    new_doc = tracker_inst.create_investigation(
                        incident_id=target_jira_key,
                        title=default_summary,
                        target_system=system_name,
                        initial_fact=observation_text,
                        initial_hypothesis=f"Initial diagnosis for {target_jira_key}.",
                    )
                    st.session_state["active_investigation_id"] = target_jira_key
                    st.session_state["active_investigation_doc"] = new_doc
                    st.success(
                        f"✅ Initialized directory `{target_jira_key}/`, saved artifact `{art_name}`, and created `analysis.md`!"
                    )
                else:
                    with st.spinner(
                        f"Appending context to `{target_jira_key}` via {selected_model}..."
                    ):
                        updated = tracker_inst.update_with_ai(
                            incident_id=target_jira_key,
                            observation=observation_text,
                            model=selected_model,
                            context_attachment=content_to_export,
                        )
                        st.session_state["active_investigation_id"] = target_jira_key
                        st.session_state["active_investigation_doc"] = updated
                        st.success(
                            f"✅ Saved artifact `{art_name}` and updated `{target_jira_key}/analysis.md`!"
                        )


def update_env_file(key: str, value: str):
    """Safely updates or inserts a key-value pair in projects/sre-hub/.env."""
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.exists():
        return
    text = env_file.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{key}=.*$", re.MULTILINE)
    line = f'{key}="{value}"'
    if pattern.search(text):
        new_text = pattern.sub(line, text)
    else:
        new_text = text.rstrip() + f"\n{line}\n"
    env_file.write_text(new_text, encoding="utf-8")
    os.environ[key] = value


st.title("SRE Operations Hub (Stateless & Zero-Waste)")

(
    tab_guide,
    tab_investigation,
    tab_mapper,
    tab_triage,
    tab_slack,
    tab_jira,
    tab_pagerduty,
    tab_gitlab,
    tab_openshift,
    tab_tekton,
    tab_export,
) = st.tabs(
    [
        "📖 How To Use This Tool",
        "🔬 Investigation & Assist Mode",
        "🗺️ Stateless Repo Mapper",
        "🔍 Log & Error Stripper",
        "💬 Slack Analyzer",
        "🎫 Jira Hub",
        "🚨 PagerDuty",
        "🦊 GitLab MR/Pipeline",
        "☸️ OpenShift",
        "🐙 Tekton",
        "📋 OpenCode Context Exporter",
    ]
)

# -----------------------------------------------------------------------------
# TAB 0: HOW TO USE THIS TOOL (SRE FUNNEL GUIDE)
# -----------------------------------------------------------------------------
with tab_guide:
    st.subheader("📖 How to Conduct Complex SRE Investigations Effectively")
    st.markdown(
        """
        ### 🎯 The "Funnel Principle" (Zero-Waste SRE)
        Never dump raw 10,000-line logs or entire codebases directly into an AI chat.
        Instead, filter locally for **0 tokens**, extract the essential core, and synthesize with targeted models.
        """
    )

    g_col1, g_col2 = st.columns(2)

    with g_col1:
        st.markdown(
            """
            #### 1️⃣ Step 1: Rapid Context (Jira, PagerDuty & Slack)
            * **Jira Hub:** Fetch an executive digest of the ticket (symptoms, components, requirements).
            * **PagerDuty:** Inspect active alert payloads, error traces, and metrics from triggered incidents.
            * **Slack Analyzer:** Analyze incident threads (`limit: 30-50`) to see if an engineer already identified a workaround or root cause.

            #### 2️⃣ Step 2: Skeleton Mapping (Repo Mapper)
            * Go to **Stateless Repo Mapper** and run a **Cold Scan** on the target repository.
            * Creates a dense, ~500-token `AI_ARCHITECTURE_MAP.md` covering playbooks, tasks, classes, and config variables with **0 AI tokens**.
            """
        )

    with g_col2:
        st.markdown(
            """
            #### 3️⃣ Step 3: Noise Stripping & Correlation (Log Stripper)
            * Paste raw terminal logs or a Jenkins `consoleText` URL.
            * Turn on **Strip ANSI** and **Extract Failure Blocks** to isolate `FAILED!`, `fatal:`, and Python tracebacks.
            * Export the sanitized error core straight into your active ticket directory.

            #### 4️⃣ Step 4: Collaborative Co-Pilot (Investigation & Assist Mode)
            * Open **🔬 Investigation & Assist Mode** for step-by-step diagnostic loops or code implementation.
            * Give prompts, evaluate test results, and receive copy-pasteable CLI commands.
            * The AI strictly maintains the 4-section persistent contract (`analysis.md`) with minimal token usage.

            #### 5️⃣ Step 5: Export to OpenCode CLI
            * Head to **OpenCode Context Exporter** and generate an **Investigation Bundle**.
            * Launch `opencode` in your target repo terminal and point it to the bundle file for immediate, accurate code fixes.
            * Run `/compact` once the investigation and fix are complete!
            """
        )

    st.divider()
    st.markdown("#### 🧠 Recommended Models for SRE Workflows")
    st.markdown(
        """
        | Task | Model | Why Choose It? |
        | :--- | :--- | :--- |
        | **Fast Triaging & Summaries** | `gemini-flash-latest` | Cloud-based, ultra-fast, massive context window, $0.001 cost. |
        | **Deep Root Cause Analysis (RCA)** | `deepseek-r1:32b` (Ollama) | Local deep-thinking model (`<think>` reasoning), excels at complex logic bugs. |
        | **Ansible / Python / Shell Fixing** | `qwen2.5-coder:32b` (Ollama) | Leading open-weight coding model with exceptional syntax accuracy. |
        | **Long Context Docs & Releases** | `command-r:35b` / `codestral:22b` | 128k context, hallucination-resistant summarization. |
        """
    )

# -----------------------------------------------------------------------------
# TAB 1: INVESTIGATION & ASSIST MODE (RCA & IMPLEMENTATION CO-PILOT)
# -----------------------------------------------------------------------------
with tab_investigation:
    st.subheader("🔬 Investigation & Implementation Assist Mode")
    st.caption(
        "Token-efficient, collaborative SRE companion. Delivers copy-paste verification commands and strictly maintains the 4-section zero-waste contract."
    )

    tracker = InvestigationTracker(storage_dir=Path(custom_inv_dir))

    # Top Header for selection/creation
    t_mode = st.radio(
        "Ticket Selection",
        ["Select Existing Investigation", "Create New Investigation (Jira Ticket)"],
        horizontal=True,
    )

    current_id = ""
    if t_mode == "Select Existing Investigation":
        c_search, c_reset = st.columns([4, 1], vertical_alignment="bottom")
        with c_search:
            search_query = st.text_input(
                "🔍 Quick Search across investigations (FTS5)",
                placeholder="Search keyword (e.g. squid, proxy, datadog, timeout)...",
                key="fts_inv_search",
            )
        with c_reset:
            if st.button("Reset Search", use_container_width=True):
                st.session_state["fts_inv_search"] = ""
                st.rerun()

        existing_investigations = tracker.list_investigations(
            search_query=search_query.strip()
            if search_query and search_query.strip()
            else None
        )
        if existing_investigations:
            default_idx = 0
            if (
                st.session_state.get("active_investigation_id")
                in existing_investigations
            ):
                default_idx = existing_investigations.index(
                    st.session_state["active_investigation_id"]
                )
            current_id = st.selectbox(
                "Select Active Jira Investigation",
                existing_investigations,
                index=default_idx,
            )
            st.session_state["active_investigation_id"] = current_id
        else:
            if search_query and search_query.strip():
                st.warning(
                    f"No investigations matched '{search_query.strip()}'. Click 'Reset Search' to show all."
                )
            else:
                st.info(
                    "No investigations found yet. Switch to 'Create New Investigation (Jira Ticket)' to start one."
                )
    else:
        doc_type_choice = st.radio(
            "Initial Contract Type",
            [
                "🔎 Root Cause Analysis (Incident / Bug)",
                "🛠️ Implementation Assistance (Feature / Fix)",
            ],
            horizontal=False,
        )
        mode_flag = (
            "implementation" if "Implementation" in doc_type_choice else "investigation"
        )

        with st.form("create_investigation_form", clear_on_submit=False):
            col_key, col_btn = st.columns([3, 1], vertical_alignment="bottom")
            with col_key:
                new_id = st.text_input(
                    "Jira Issue Key",
                    placeholder="e.g. SPRE-6318 or KFLUXSPRT-1234",
                    help="You only need to provide the Jira Key. Title, system, and existing notes will be detected automatically.",
                )
            with col_btn:
                submit_create = st.form_submit_button(
                    "🚀 Initialize Contract", type="primary", use_container_width=True
                )

            with st.expander(
                "⚙️ Optional Customization (Title, System, Manual Notes)",
                expanded=False,
            ):
                c_opt1, c_opt2 = st.columns(2)
                with c_opt1:
                    new_title = st.text_input(
                        "Custom Title / Summary",
                        placeholder="Automatically extracted if empty",
                    )
                with c_opt2:
                    new_sys = st.text_input(
                        "Target System / Repository",
                        placeholder="Automatically detected if empty",
                    )

                new_spec = st.text_area(
                    "Initial facts or specification (optional):",
                    placeholder="What do we know so far?",
                )
                new_next = st.text_input(
                    "Initial hypothesis or next step (optional):",
                    placeholder="What should we test or build first?",
                )
                scan_dir = st.checkbox(
                    "🔍 Scan folder and synthesize from existing files",
                    value=True,
                    help="If notes (PLAN.md, README.md, playbooks, etc.) already exist in the ticket folder, this will import and synthesize them into the contract.",
                )

        if submit_create:
            if not new_id.strip():
                st.warning("Please provide a Jira Issue Key (e.g. SPRE-6318).")
            else:
                jira_clean = new_id.strip().upper()
                with st.spinner(
                    f"Initializing & synthesizing {jira_clean} state contract..."
                ):
                    new_content = tracker.create_investigation(
                        incident_id=jira_clean,
                        title=new_title.strip(),
                        target_system=new_sys.strip() or "Unspecified",
                        mode=mode_flag,
                        initial_fact=new_spec.strip(),
                        initial_hypothesis=new_next.strip(),
                        scan_directory=scan_dir,
                    )
                st.session_state["active_investigation_id"] = jira_clean
                st.session_state["active_investigation_doc"] = new_content
                st.success(
                    f"Initialized {mode_flag.upper()} contract `{jira_clean}/analysis.md`!"
                )
                st.rerun()

    if current_id:
        try:
            curr_content = tracker.read_investigation(current_id)
        except Exception as e:
            st.error(f"⚠️ Could not load investigation '{current_id}': {e}")
            st.info(
                "Tip: You can use 'Create New Investigation (Jira Ticket)' with this key to synthesize or initialize its contract."
            )
            st.stop()

        is_doc_impl = tracker.is_implementation_doc(curr_content)

        st.divider()

        # Mode switcher & Focus Control
        col_mode_sel, col_mode_conv = st.columns(
            [1.1, 0.9], vertical_alignment="center"
        )
        with col_mode_sel:
            active_focus = st.radio(
                "Active Co-Pilot Mode:",
                ["🔎 Investigation (RCA / Triage)", "🛠️ Implementation Assist"],
                index=1 if is_doc_impl else 0,
                horizontal=True,
                help="Determines the assistant's perspective and the 4-section contract schema.",
            )
        with col_mode_conv:
            if not is_doc_impl and active_focus == "🛠️ Implementation Assist":
                if st.button(
                    "🔄 Convert Ticket to Implementation Plan",
                    help="Archives RCA and switches schema to SPEC, BASELINE, FAILED, NEXT STEP",
                ):
                    new_plan = tracker.convert_to_implementation(current_id)
                    st.session_state["active_investigation_doc"] = new_plan
                    st.success("Converted to Implementation Contract!")
                    st.rerun()

        # UI LAYOUT REFACTOR: Left is State/Contract, Right is Chat Flow
        col_contract, col_chat = st.columns([1, 1.2], gap="medium")

        # LEFT COLUMN - THE CONTRACT (STATE)
        with col_contract:
            doc_path = tracker.get_file_path(current_id)
            st.markdown("#### 📄 Active State Contract")
            st.caption(f"📁 Path: `{doc_path}`")

            edit_mode = st.toggle("✏️ Manual Edit Mode (Raw Markdown)", value=False)
            if edit_mode:
                manual_text = st.text_area(
                    "Edit Raw Content", value=curr_content, height=600
                )
                if st.button("💾 Save Manual Edits", use_container_width=True):
                    tracker.save_raw_investigation(current_id, manual_text)
                    st.session_state["active_investigation_doc"] = manual_text
                    st.success("Changes saved!")
                    st.rerun()
            else:
                with st.container(height=650, border=True):
                    st.markdown(curr_content)

            artifacts = tracker.list_artifacts(current_id)
            if artifacts:
                with st.expander(
                    f"📦 Collected Artifacts in `{current_id}/` ({len(artifacts)})",
                    expanded=False,
                ):
                    for art in artifacts:
                        art_name = art.name
                        with st.popover(
                            f"📄 {art_name} ({round(art.stat().st_size / 1024, 1)} KB)"
                        ):
                            try:
                                art_content = art.read_text(encoding="utf-8")
                                suffix = (
                                    "\n... [truncated]"
                                    if len(art_content) > 2000
                                    else ""
                                )
                                st.code(
                                    art_content[:2000] + suffix, language="markdown"
                                )
                            except Exception as e:
                                st.caption(f"Binary or unreadable artifact: {e}")

            # Related Jira Issues & Dependencies
            relations = tracker.get_ticket_relations(current_id)
            with st.expander(
                f"🔗 Related Tickets & Dependencies ({len(relations)})",
                expanded=bool(relations),
            ):
                c_rel_sync, c_rel_add = st.columns([1, 1])
                with c_rel_sync:
                    if st.button(
                        "🔄 Sync from Jira API",
                        use_container_width=True,
                        help="Fetches all linked tickets (issuelinks, parent, subtasks) from Jira API",
                    ):
                        from tools.jira_relations import sync_jira_relations

                        with st.spinner("Fetching Jira relations..."):
                            synced = sync_jira_relations(current_id)
                            st.success(f"Synced {len(synced)} relations from Jira!")
                            st.rerun()

                with c_rel_add:
                    with st.popover("➕ Manual Link", use_container_width=True):
                        rel_target = (
                            st.text_input("Target Key", placeholder="e.g. SPRE-4573")
                            .strip()
                            .upper()
                        )
                        rel_type = st.selectbox(
                            "Relation Type",
                            [
                                "is_blocked_by",
                                "blocks",
                                "relates_to",
                                "parent",
                                "subtask",
                                "duplicates",
                                "remediates_snow_vul",
                            ],
                        )
                        rel_summary = st.text_input(
                            "Summary / Note",
                            placeholder="e.g. Squid proxy firewall request",
                        )
                        rel_status = st.selectbox(
                            "Target Status",
                            ["In Progress", "Open", "Closed", "Resolved", "Blocked"],
                        )
                        if st.button("Link Ticket", use_container_width=True):
                            if rel_target:
                                tracker.add_ticket_relation(
                                    current_id,
                                    rel_target,
                                    rel_type,
                                    rel_summary,
                                    rel_status,
                                )
                                st.success(f"Linked {current_id} -> {rel_target}")
                                st.rerun()

                if relations:
                    for rel in relations:
                        rel_label = rel["relation_type"].replace("_", " ").title()
                        st.markdown(
                            f"- **{rel_label}**: [{rel['target_key']}](https://redhat.atlassian.net/browse/{rel['target_key']}) `{rel.get('status', '')}` {rel.get('summary', '')}"
                        )

            # ServiceNow Integration & Drafter
            with st.expander(
                "🎫 ServiceNow Assistant (CHG / Request / VUL)",
                expanded=True if "VUL" in curr_content else False,
            ):
                st.caption(
                    "Import ServiceNow tickets content, check status and automatically generate RFC / Change drafts."
                )

                # If this is a vulnerability remediation ticket (e.g. SPRE-6677 with VUL/VIT)
                if "VUL" in curr_content or "patching" in curr_content.lower():
                    st.info(
                        "🛡️ **Qualys Vulnerability Ticket Detected** (with VUL / VIT linked elements)"
                    )
                    if st.button(
                        "🔍 Analyze Vulnerability Background & CVE Investigation Plan",
                        use_container_width=True,
                        type="secondary",
                    ):
                        from tools.servicenow_tool import analyze_vulnerability_ticket

                        with st.spinner(
                            "Analyzing Qualys vulnerability group, compliance deadlines & CVE check commands..."
                        ):
                            vul_analysis = analyze_vulnerability_ticket(current_id)
                            st.session_state[
                                f"vul_analysis_{current_id}"
                            ] = vul_analysis
                            st.success(
                                "Vulnerability analysis created and attached to ticket artifacts!"
                            )
                            st.rerun()

                    if st.session_state.get(f"vul_analysis_{current_id}"):
                        with st.popover("📖 View: Vulnerability & CVE Analysis"):
                            st.markdown(st.session_state[f"vul_analysis_{current_id}"])
                    st.divider()

                c_sn_type, c_sn_gen = st.columns([1, 1], vertical_alignment="bottom")
                with c_sn_type:
                    chg_type_choice = st.selectbox(
                        "RFC Type",
                        [
                            "Normal Change",
                            "Standard Change",
                            "Emergency Change",
                            "Service Request / Access",
                        ],
                        key="snow_chg_type_sel",
                    )
                with c_sn_gen:
                    if st.button(
                        "🚀 Draft ServiceNow RFC",
                        use_container_width=True,
                        type="primary",
                    ):
                        from tools.servicenow_tool import draft_snow_change_from_jira

                        with st.spinner(
                            f"Generating ServiceNow {chg_type_choice} draft with Gemini..."
                        ):
                            draft_out = draft_snow_change_from_jira(
                                current_id, request_type=chg_type_choice
                            )
                            st.session_state[f"snow_draft_{current_id}"] = draft_out
                            st.success(
                                "ServiceNow RFC draft created and saved to artifacts!"
                            )
                            st.rerun()

                if st.session_state.get(f"snow_draft_{current_id}"):
                    st.markdown("#### 📋 Generated ServiceNow Submission Draft")
                    st.code(
                        st.session_state[f"snow_draft_{current_id}"],
                        language="markdown",
                    )

                st.divider()
                st.markdown(
                    "**🔗 Import External ServiceNow Ticket (CHG / INC / RITM):**"
                )
                st.caption(
                    "If someone else opened the ticket, provide the number here to import its full content (plan, description, window, notes)."
                )
                c_sn_in, c_sn_btn, c_sn_import = st.columns(
                    [2, 1, 1.5], vertical_alignment="bottom"
                )
                with c_sn_in:
                    snow_input_num = st.text_input(
                        "ServiceNow Number",
                        placeholder="e.g. CHG4504902 or INC0987654",
                        key="snow_link_input",
                    )
                with c_sn_btn:
                    if st.button("Verify & Link", use_container_width=True):
                        if snow_input_num.strip():
                            from tools.servicenow_tool import link_snow_record

                            with st.spinner(
                                f"Verifying {snow_input_num.strip()} via ServiceNow API..."
                            ):
                                try:
                                    linked_rec = link_snow_record(
                                        current_id, snow_input_num.strip()
                                    )
                                    st.success(
                                        f"Linked {linked_rec['number']} ({linked_rec['type']}): {linked_rec['summary']} [{linked_rec['state']}]"
                                    )
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error linking record: {e}")
                with c_sn_import:
                    if st.button(
                        "📥 Import Full Content",
                        use_container_width=True,
                        help="Downloads the entire ticket (plan, description, work notes) as an artifact",
                    ):
                        if snow_input_num.strip():
                            from tools.servicenow_tool import (
                                import_snow_record_to_investigation,
                            )

                            with st.spinner(
                                f"Fetching full ServiceNow content for {snow_input_num.strip()}..."
                            ):
                                try:
                                    imported = import_snow_record_to_investigation(
                                        current_id, snow_input_num.strip()
                                    )
                                    st.success(
                                        f"Imported {imported['number']} into {imported['artifact_file']}!"
                                    )
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error importing record: {e}")

        # RIGHT COLUMN - THE CO-PILOT CHAT FLOW
        with col_chat:
            st.markdown(f"#### 💬 Co-Pilot Chat (`{current_id}`)")

            hist_key = f"dialog_history_{current_id}"
            if hist_key not in st.session_state:
                st.session_state[hist_key] = []
                # Restore persistent chat history from SQLite
                db_msgs = tracker.get_chat_history(current_id)
                for i in range(len(db_msgs)):
                    m = db_msgs[i]
                    if m["role"] == "user":
                        resp = (
                            db_msgs[i + 1]["message"]
                            if (
                                i + 1 < len(db_msgs)
                                and db_msgs[i + 1]["role"] == "assistant"
                            )
                            else ""
                        )
                        st.session_state[hist_key].append(
                            {
                                "time": m.get("created_at", "")[11:19]
                                if m.get("created_at")
                                else "",
                                "prompt": m["message"],
                                "response": resp,
                                "mode": "chat",
                            }
                        )

            # Context Attachment Expander
            with st.expander("📎 Token-Saving Context Attachment", expanded=False):
                context_parts = []

                has_sanitized = bool(st.session_state.get("sanitized_error"))
                attach_stripped = st.checkbox(
                    "Attach Stripped Error Core (from Log Stripper tab)",
                    value=has_sanitized,
                )
                if attach_stripped and st.session_state.get("sanitized_error"):
                    context_parts.append(
                        f"### STRIPPED ERROR LOG:\n{st.session_state['sanitized_error']}"
                    )

                ticket_artifacts = tracker.list_artifacts(current_id)
                if ticket_artifacts:
                    art_names = ["(None)"] + [a.name for a in ticket_artifacts]
                    chosen_art = st.selectbox("Attach Ticket Artifact:", art_names)
                    if chosen_art != "(None)":
                        art_file = tracker.get_ticket_dir(current_id) / chosen_art
                        if art_file.exists():
                            try:
                                context_parts.append(
                                    f"### ARTIFACT [{chosen_art}]:\n{art_file.read_text(encoding='utf-8')[:3500]}"
                                )
                            except Exception:
                                pass

                pasted_output = st.text_area(
                    "Specific raw command output / traceback snippet:", height=80
                )
                if pasted_output.strip():
                    context_parts.append(
                        f"### TERMINAL OUTPUT / SNIPPET:\n{pasted_output.strip()[:3500]}"
                    )

                final_context_attachment = "\n\n".join(context_parts)

            col_c1, col_c2 = st.columns([1, 1])
            with col_c1:
                st.caption(
                    f"Micro-Window Load: **{len(st.session_state[hist_key][-3:])}** msgs + State"
                )
            with col_c2:
                if st.session_state[hist_key]:
                    if st.button("🧹 Clear Chat History", use_container_width=True):
                        st.session_state[hist_key] = []
                        st.rerun()

            # Chat Container
            chat_container = st.container(height=520, border=True)
            with chat_container:
                if not st.session_state[hist_key]:
                    st.caption(
                        "No recent conversation history. Start typing your observations below."
                    )
                for msg in st.session_state[hist_key]:
                    with st.chat_message("user"):
                        st.write(msg["prompt"])
                    with st.chat_message("assistant"):
                        st.write(msg["response"])

            # Chat Input Form
            if prompt := st.chat_input("Prompt, instruction, or command output..."):
                # Append user prompt immediately
                st.session_state[hist_key].append(
                    {
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "prompt": prompt,
                        "response": "...",  # placeholder
                        "mode": active_focus,
                    }
                )
                st.rerun()  # Quick rerun to update UI

            # If the last message is a placeholder, perform the AI call
            if (
                st.session_state[hist_key]
                and st.session_state[hist_key][-1]["response"] == "..."
            ):
                last_msg = st.session_state[hist_key][-1]
                user_comm = last_msg["prompt"]

                with chat_container:
                    with st.chat_message("assistant"):
                        with st.spinner(
                            f"Consulting {selected_global_model} & updating State Contract..."
                        ):
                            # Extract the micro-window history (last 3 interactions, excluding current placeholder)
                            micro_window = []
                            for m in st.session_state[hist_key][-4:-1]:
                                micro_window.append(
                                    {"role": "user", "content": m["prompt"]}
                                )
                                micro_window.append(
                                    {"role": "assistant", "content": m["response"]}
                                )

                            interact_res = tracker.interact_with_ai(
                                incident_id=current_id,
                                user_message=user_comm,
                                model=selected_global_model,
                                context_attachment=final_context_attachment,
                                mode="implementation"
                                if "Implementation" in active_focus
                                else "investigation",
                                chat_history=micro_window,
                            )

                            # Replace placeholder with real response
                            st.session_state[hist_key][-1]["response"] = interact_res[
                                "response"
                            ]
                            st.session_state["active_investigation_doc"] = interact_res[
                                "updated_doc"
                            ]
                            st.rerun()


# -----------------------------------------------------------------------------
# TAB 2: STATELESS REPO MAPPER
# -----------------------------------------------------------------------------
with tab_mapper:
    st.subheader("Repository Architecture Map Generator")

    col_in, col_out = st.columns([1, 1])

    with col_in:
        repo_options = ["(Custom Path)"] + DEFAULT_REPOS
        default_index = 1 if len(DEFAULT_REPOS) > 0 else 0
        repo_choice = st.selectbox(
            f"Select Repository from {DEFAULT_REPOS_DIR}",
            repo_options,
            index=default_index,
        )

        if repo_choice == "(Custom Path)":
            selected_repo = st.text_input(
                "Enter custom repository path", value=str(DEFAULT_REPOS_DIR)
            )
        else:
            selected_repo = repo_choice
            st.caption(f"Active: `{selected_repo}`")

        mode = st.radio(
            "Scan Strategy",
            [
                "Cold Scan (Structure, AST, Ansible Vars)",
                "Incremental Diff (HEAD~1..HEAD)",
            ],
        )

        st.caption(f"Engine: `{selected_global_model}` (configurable in sidebar)")

        if st.button("1. Extract Local Skeleton (0 Tokens)", use_container_width=True):
            try:
                if "Cold Scan" in mode:
                    scanner = RepoScanner(selected_repo)
                    st.session_state["repo_payload"] = scanner.build_cold_payload()
                else:
                    sanitizer = DiffSanitizer(selected_repo)
                    st.session_state["repo_payload"] = sanitizer.get_incremental_diff()
                st.success("Local extraction completed without AI tokens.")
            except Exception as e:
                st.error(f"Extraction error: {e}")

        if st.session_state["repo_payload"]:
            st.text_area(
                "Prepared Input Payload", st.session_state["repo_payload"], height=250
            )

    with col_out:
        repo_name = Path(selected_repo).name
        saved_map_path = MAPS_DIR / f"{repo_name}.md"

        if st.button(
            "2. Generate / Update Map via LLM", type="primary", use_container_width=True
        ):
            if not st.session_state["repo_payload"]:
                st.warning("Please extract local skeleton or diff first.")
            else:
                with st.spinner(f"Calling {selected_global_model}..."):
                    system_prompt = (
                        "You are a Principal Software Architect. Produce a complete, highly dense, "
                        "deterministic AI_ARCHITECTURE_MAP.md. Avoid conversational text. "
                        "Include: 1. COMPONENT DIRECTORY MATRIX, 2. VARIABLE & CONFIGURATION SURFACE, "
                        "3. CONTROL FLOW & ENTRY POINTS, 4. KNOWN HOTSPOTS & ERROR CONTRACTS."
                    )

                    prompt_content = st.session_state["repo_payload"]
                    if "Incremental" in mode and saved_map_path.exists():
                        existing_map = saved_map_path.read_text(encoding="utf-8")
                        prompt_content = f"# EXISTING MAP:\n{existing_map}\n\n# GIT DIFF:\n{prompt_content}"

                    out = LLMBridge.execute(
                        prompt_content,
                        model=selected_global_model,
                        system_prompt=system_prompt,
                    )
                    st.session_state["generated_map"] = out
                    saved_map_path.write_text(out, encoding="utf-8")
                    st.success(f"Map updated and saved to: {saved_map_path}")

        if saved_map_path.exists() and not st.session_state["generated_map"]:
            st.session_state["generated_map"] = saved_map_path.read_text(
                encoding="utf-8"
            )

        if st.session_state["generated_map"]:
            st.text_area(
                "AI_ARCHITECTURE_MAP.md (Current Truth)",
                st.session_state["generated_map"],
                height=350,
            )

            render_export_to_tracker(
                tracker_inst=tracker,
                content_to_export=st.session_state["generated_map"],
                default_summary=f"Architecture Map for {repo_name}",
                observation_text=f"Loaded Architecture Map for repository '{repo_name}'.",
                key_prefix="mapper",
                selected_model=selected_global_model,
                system_name=repo_name,
            )

# -----------------------------------------------------------------------------
# TAB 2: LOG & ERROR STRIPPER (Terminal, Ansible, OpenShift & Jenkins)
# -----------------------------------------------------------------------------
with tab_triage:
    st.subheader("Error Core Stripper & Investigation Triage")

    col_t1, col_t2 = st.columns([1, 1])

    with col_t1:
        log_source = st.radio(
            "Log Source",
            ["Direct Paste (Terminal / Logs)", "Jenkins Build URL"],
            horizontal=True,
        )
        raw_log_input = ""

        if log_source == "Direct Paste (Terminal / Logs)":
            raw_log_input = st.text_area(
                "Paste Raw Massive Logs",
                height=250,
                placeholder="Paste console output, Ansible runs, Jenkins logs here...",
            )
        else:
            jenkins_url = st.text_input(
                "Jenkins Build URL",
                placeholder="https://jenkins.example.com/job/my-pipeline/42/ (or .../console)",
            )
            c_auth1, c_auth2 = st.columns(2)
            with c_auth1:
                default_jk_user = (
                    os.getenv("JENKINS_USER")
                    or os.getenv("JENKINS_USERNAME")
                    or os.getenv("JIRA_EMAIL", "").split("@")[0]
                )
                jk_user = st.text_input(
                    "Jenkins Username",
                    value=default_jk_user,
                    help="Your Jenkins/Kerberos username.",
                )
            with c_auth2:
                default_jk_token = (
                    os.getenv("JENKINS_TOKEN") or os.getenv("JENKINS_API_TOKEN") or ""
                )
                jk_token = st.text_input(
                    "Jenkins API Token",
                    value=default_jk_token,
                    type="password",
                    help="Jenkins User Profile -> Configure -> API Token.",
                )

            save_jenkins_env = st.checkbox(
                "💾 Remember credentials in .env",
                value=bool(os.getenv("JENKINS_TOKEN")),
                help="Saves JENKINS_USER and JENKINS_TOKEN to projects/sre-hub/.env",
            )

            if st.button("Fetch Jenkins Console Log", use_container_width=True):
                if not jenkins_url.strip():
                    st.warning("Please provide a Jenkins Build URL.")
                else:
                    if save_jenkins_env and jk_token.strip():
                        if jk_user.strip():
                            update_env_file("JENKINS_USER", jk_user.strip())
                        update_env_file("JENKINS_TOKEN", jk_token.strip())

                    with st.spinner("Downloading consoleText from Jenkins..."):
                        fetched = LogSanitizer.fetch_jenkins_log(
                            jenkins_url, user=jk_user, token=jk_token
                        )
                        if fetched.startswith("Error"):
                            st.error(fetched)
                        else:
                            raw_log_input = fetched
                            st.session_state["raw_jenkins_log"] = fetched
                            lines_cnt = len(fetched.splitlines())
                            st.success(
                                f"✅ Successfully fetched {lines_cnt:,} lines from Jenkins console!"
                            )

            if "raw_jenkins_log" in st.session_state and not raw_log_input:
                raw_log_input = st.session_state["raw_jenkins_log"]

        context_lines = st.slider(
            "Context lines around error keywords", min_value=1, max_value=10, value=3
        )

        if st.button("Extract Error Core Locally (0 Tokens)", use_container_width=True):
            if raw_log_input:
                core = LogSanitizer.extract_error_core(
                    raw_log_input, context_lines=context_lines
                )
                st.session_state["sanitized_error"] = core
                original_len = len(raw_log_input.splitlines())
                core_len = len(core.splitlines())
                st.info(
                    f"Stripped from {original_len} lines down to {core_len} lines ({(1 - core_len/max(1, original_len))*100:.1f}% reduction)."
                )

    with col_t2:
        if st.session_state["sanitized_error"]:
            st.text_area(
                "Sanitized Error Core (Local Stripper Output)",
                st.session_state["sanitized_error"],
                height=180,
            )

            st.markdown("#### Correlate with Source Repository")
            correlate_repo = st.selectbox(
                "Select Related Source Repo (Provides architecture context to triage)",
                ["(None)"] + DEFAULT_REPOS,
                index=1 if len(DEFAULT_REPOS) > 0 else 0,
            )

            st.caption(
                f"Triage Engine: `{selected_global_model}` (configurable in sidebar)"
            )

            if st.button(
                "Triage with Repo Context (< $0.001)",
                type="primary",
                use_container_width=True,
            ):
                with st.spinner("Analyzing error core against repository context..."):
                    repo_context = ""
                    if correlate_repo != "(None)":
                        c_name = Path(correlate_repo).name
                        c_map_file = MAPS_DIR / f"{c_name}.md"
                        if c_map_file.exists():
                            repo_context = f"\n\n# REPOSITORY ARCHITECTURE MAP ({c_name}):\n{c_map_file.read_text(encoding='utf-8')[:3000]}"
                        else:
                            repo_context = f"\n\n# REPOSITORY PATH: {correlate_repo} (Map not yet generated in state/maps/)"

                    payload = f"# SANITIZED ERROR LOG:\n{st.session_state['sanitized_error']}{repo_context}"
                    sys_prompt = (
                        "You are a Senior Principal SRE. Analyze the error log in the context of the provided source repository. "
                        "Pinpoint root cause, affected components/files, and exact remediation steps in concise bullet points. "
                        "Do not include conversational filler."
                    )
                    res = LLMBridge.execute(
                        payload, model=selected_global_model, system_prompt=sys_prompt
                    )
                    st.session_state["triage_result"] = res

        if st.session_state["triage_result"]:
            st.markdown("### Root Cause & Remediation")
            st.markdown(st.session_state["triage_result"])

            render_export_to_tracker(
                tracker_inst=tracker,
                content_to_export=st.session_state["triage_result"],
                default_summary="Log and Error Triage Findings",
                observation_text="Log stripping and RCA findings:\n"
                + st.session_state["triage_result"][:500],
                key_prefix="triage",
                selected_model=selected_global_model,
                system_name=Path(correlate_repo).name
                if correlate_repo != "(None)"
                else "Logs",
            )
        elif st.session_state.get("sanitized_error"):
            render_export_to_tracker(
                tracker_inst=tracker,
                content_to_export=st.session_state["sanitized_error"],
                default_summary="Sanitized Log Error Core",
                observation_text="Isolated Error Core from logs:\n"
                + st.session_state["sanitized_error"][:500],
                key_prefix="triage_raw",
                selected_model=selected_global_model,
                system_name="Logs",
            )

# -----------------------------------------------------------------------------
# TAB 3: SLACK ANALYZER (tools/slack_analyze.py)
# -----------------------------------------------------------------------------
with tab_slack:
    st.subheader("Slack Thread & Channel Analysis")

    with st.expander(
        "💡 Slack Authentication Setup (Where to get tokens?)", expanded=False
    ):
        st.markdown(
            """
            To read Slack messages and threads without an expensive bot app installation, this tool uses your authenticated web session tokens:

            1. **`SLACK_XOXC_TOKEN`**: Starts with `xoxc-...` (found in browser `localStorage` or network requests).
            2. **`SLACK_XOXD_TOKEN`**: Starts with `xoxd-...` (stored in the browser cookie named `d`).

            #### How to extract:
            * **Manual (Browser DevTools):**
              1. Open your Slack workspace in Chrome/Firefox.
              2. Press `F12` -> Application/Storage tab -> Cookies -> Copy `d` cookie (`xoxd-...`).
              3. In DevTools Console, run: `JSON.parse(localStorage.localConfig_v2).teams[window.TS.model.team.id].token` to get `xoxc-...`.
            * **Automated via Companion Extractor:**
              If you have the `slack-token-extractor` utility in your environment (`state/maps/slack-token-extractor.md`), you can extract them via Playwright or SQLite directly from your browser profile.

            Export them to your shell profile (`~/.bashrc`) or project `.env`:
            ```bash
            export SLACK_WORKSPACE_URL="https://your-workspace.enterprise.slack.com"
            export SLACK_XOXC_TOKEN="xoxc-..."
            export SLACK_XOXD_TOKEN="xoxd-..."
            ```
            """
        )

    col_s1, col_s2 = st.columns([1, 1])

    with col_s1:
        slack_url = st.text_input(
            "Channel ID or Thread URL",
            placeholder=f"{SLACK_WORKSPACE_URL}/archives/C0123456789/p1787725040262099",
        )
        slack_prompt = st.text_area(
            "Analysis Instruction",
            value=f"Please summarize the discussion in this channel/thread. What is the main topic? Specifically, check if {SRE_USER_HANDLE} is being addressed or mentioned. What is needed or requested from me?",
        )
        msg_limit = st.number_input(
            "Message Limit", min_value=10, max_value=200, value=50, step=10
        )
        search_kw = st.text_input(
            "Optional Keyword Filter", placeholder="incident, blocker..."
        )

        if st.button("Run Slack Analysis", type="primary", use_container_width=True):
            if not slack_url:
                st.warning("Please enter a valid Slack Channel ID or Thread URL.")
            else:
                with st.spinner("Executing slack analysis locally..."):
                    out = SREExternalTools.run_slack_analyze(
                        target=slack_url,
                        prompt=slack_prompt,
                        limit=msg_limit,
                        search=search_kw,
                    )
                    st.session_state["slack_result"] = out

    with col_s2:
        if st.session_state["slack_result"]:
            st.markdown("### Slack Analysis Result")
            st.markdown(st.session_state["slack_result"])

            render_export_to_tracker(
                tracker_inst=tracker,
                content_to_export=st.session_state["slack_result"],
                default_summary=f"Slack Analysis: {slack_url[:40]}",
                observation_text="Slack thread findings:\n"
                + st.session_state["slack_result"][:500],
                key_prefix="slack",
                selected_model=selected_global_model,
                system_name="Slack",
            )


def parse_jira_summarize_output(raw_text: str) -> dict:
    """Parses output of jira_gemini.py summarize into structured sections and issues."""
    sections = {"in_progress": [], "in_review": [], "other": [], "analysis": ""}
    if not raw_text:
        return sections

    parts = raw_text.split("GEMINI ANALYSIS")
    tickets_part = parts[0]
    if len(parts) > 1:
        sections["analysis"] = parts[1].strip("=\n \r")

    current_sec = None
    for line in tickets_part.splitlines():
        line_strip = line.strip()
        if "IN PROGRESS" in line_strip:
            current_sec = "in_progress"
        elif "IN REVIEW" in line_strip:
            current_sec = "in_review"
        elif "OTHER" in line_strip:
            current_sec = "other"
        elif line_strip.startswith("===") or not line_strip:
            continue
        elif current_sec and ("—" in line or "-" in line):
            delimiter = "—" if "—" in line else "-"
            chunks = line_strip.split(delimiter, 1)
            key = chunks[0].strip()
            summary = chunks[1].strip() if len(chunks) > 1 else ""
            if key and len(key) < 25:
                sections[current_sec].append({"key": key, "summary": summary})

    return sections


# -----------------------------------------------------------------------------
# TAB 4: JIRA HUB (tools/jira_gemini.py)
# -----------------------------------------------------------------------------
with tab_jira:
    st.subheader("🎫 Jira Hub & Workload Intelligence")
    st.caption(
        "Inspect your active workload, summarize assigned tickets with Gemini Flash, or analyze specific issues."
    )

    sub_jira_summary, sub_jira_search = st.tabs(
        [
            "📊 My Active Work & Daily Report (summarize)",
            "🔎 Single Ticket Analysis & JQL Search",
        ]
    )

    with sub_jira_summary:
        st.markdown("#### 📋 Assigned Tasks & Strategic Priority Report")
        st.caption(
            "Directly runs `jira_gemini.py summarize` to fetch your open Jira tickets and synthesize priorities, blockers, and next steps."
        )

        col_sum_btn, col_sum_opt = st.columns([1, 1], vertical_alignment="center")
        with col_sum_btn:
            run_summary_btn = st.button(
                "🚀 Fetch My Tickets & Generate Gemini Report",
                type="primary",
                use_container_width=True,
            )
        with col_sum_opt:
            if st.session_state.get("jira_summarize_result"):
                if st.button("🗑️ Clear Report", use_container_width=True):
                    st.session_state["jira_summarize_result"] = ""
                    st.rerun()

        if run_summary_btn:
            with st.spinner(
                "Connecting to Jira & analyzing with Gemini Flash (via jira_gemini.py summarize)..."
            ):
                out = SREExternalTools.run_jira_command(mode="summarize")
                st.session_state["jira_summarize_result"] = out
                st.rerun()

        if st.session_state.get("jira_summarize_result"):
            raw_summary = st.session_state["jira_summarize_result"]
            parsed_summary = parse_jira_summarize_output(raw_summary)

            # Metric cards
            st.divider()
            m_col1, m_col2, m_col3 = st.columns(3)
            with m_col1:
                st.metric("🚀 In Progress", len(parsed_summary["in_progress"]))
            with m_col2:
                st.metric("👀 In Review", len(parsed_summary["in_review"]))
            with m_col3:
                st.metric("📋 Backlog / Other", len(parsed_summary["other"]))

            col_tickets_view, col_analysis_view = st.columns([1.1, 1.2])

            with col_tickets_view:
                st.markdown("##### 📌 My Current Task Breakdown")

                # IN PROGRESS
                if parsed_summary["in_progress"]:
                    st.markdown("**🚀 In Progress:**")
                    for t in parsed_summary["in_progress"]:
                        t_key = t["key"]
                        t_sum = t["summary"]
                        c_t1, c_t2 = st.columns(
                            [0.75, 0.25], vertical_alignment="center"
                        )
                        with c_t1:
                            st.markdown(
                                f"**[{t_key}](https://issues.redhat.com/browse/{t_key})** — {t_sum}"
                            )
                        with c_t2:
                            if st.button(
                                "🎯 Investigate",
                                key=f"inv_btn_{t_key}",
                                help=f"Set {t_key} as active ticket in Investigation & Assist Mode",
                            ):
                                st.session_state["active_investigation_id"] = t_key
                                st.success(
                                    f"Selected `{t_key}`! Switch to Tab 2 to start."
                                )
                    st.markdown("---")

                # IN REVIEW
                if parsed_summary["in_review"]:
                    st.markdown("**👀 In Review:**")
                    for t in parsed_summary["in_review"]:
                        t_key = t["key"]
                        t_sum = t["summary"]
                        c_t1, c_t2 = st.columns(
                            [0.75, 0.25], vertical_alignment="center"
                        )
                        with c_t1:
                            st.markdown(
                                f"**[{t_key}](https://issues.redhat.com/browse/{t_key})** — {t_sum}"
                            )
                        with c_t2:
                            if st.button(
                                "🎯 Investigate",
                                key=f"inv_btn_{t_key}",
                                help=f"Set {t_key} as active ticket in Investigation & Assist Mode",
                            ):
                                st.session_state["active_investigation_id"] = t_key
                                st.success(
                                    f"Selected `{t_key}`! Switch to Tab 2 to start."
                                )
                    st.markdown("---")

                # OTHER
                if parsed_summary["other"]:
                    with st.expander(
                        f"📋 Other Assigned ({len(parsed_summary['other'])})",
                        expanded=False,
                    ):
                        for t in parsed_summary["other"]:
                            t_key = t["key"]
                            t_sum = t["summary"]
                            st.markdown(
                                f"&bull; **[{t_key}](https://issues.redhat.com/browse/{t_key})** — {t_sum}"
                            )

            with col_analysis_view:
                st.markdown("##### 🧠 Gemini Strategic Workload Analysis")
                if parsed_summary["analysis"]:
                    st.markdown(
                        """
                        <div style="background-color: rgba(33, 40, 54, 0.7); border-left: 4px solid #38a169; padding: 14px 18px; border-radius: 6px; margin-bottom: 14px;">
                            <strong style="color: #68d391; font-size: 1.05rem;">📊 Executive Priorities & Blockers:</strong>
                            <div style="margin-top: 8px; font-size: 0.92rem; line-height: 1.5;">
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    st.markdown(parsed_summary["analysis"])
                else:
                    st.info("No Gemini analysis block found.")

                with st.expander("📄 View Raw Output & Download", expanded=False):
                    st.code(raw_summary, language="text")
                    st.download_button(
                        label="📥 Download Daily Work Report (.md)",
                        data=raw_summary,
                        file_name="jira_workload_report.md",
                        mime="text/markdown",
                        use_container_width=True,
                    )

            render_export_to_tracker(
                tracker_inst=tracker,
                content_to_export=raw_summary,
                default_summary="Jira Workload Report (Assigned Tickets)",
                observation_text="Daily Jira Workload Summary:\n"
                + (
                    parsed_summary["analysis"][:500]
                    if parsed_summary["analysis"]
                    else raw_summary[:500]
                ),
                key_prefix="jira_sum",
                selected_model=selected_global_model,
                system_name="Jira",
            )

    with sub_jira_search:
        col_ctrl1, col_ctrl2 = st.columns([1, 2])

        with col_ctrl1:
            jira_action = st.radio(
                "Operation", ["Analyze Single Ticket", "Search JQL"], horizontal=False
            )

        with col_ctrl2:
            target_param = ""
            if jira_action == "Analyze Single Ticket":
                target_param = st.text_input(
                    "Issue Key", placeholder="SPRE-1234 or KFLUXSPRT-567"
                )
            elif jira_action == "Search JQL":
                target_param = st.text_input(
                    "JQL Query", placeholder='project = SPRE AND status = "In Progress"'
                )

            run_jira_btn = st.button(
                "Run Jira Command", type="primary", use_container_width=True
            )

        if run_jira_btn:
            mode_map = {"Analyze Single Ticket": "analyze", "Search JQL": "search"}
            with st.spinner("Calling Jira + Gemini..."):
                out = SREExternalTools.run_jira_command(
                    mode=mode_map[jira_action], target=target_param
                )
                st.session_state["jira_result"] = out

        if st.session_state.get("jira_result"):
            st.divider()
            st.markdown("### Jira Digest")
            st.markdown(
                f"""
                <div style="font-size: 0.88rem; line-height: 1.5; background-color: rgba(28, 31, 38, 0.5); padding: 16px; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.1);">
                    {st.session_state["jira_result"]}
                </div>
                """,
                unsafe_allow_html=True,
            )

            single_key_preset = (
                target_param.strip().upper()
                if (jira_action == "Analyze Single Ticket" and target_param.strip())
                else ""
            )
            if single_key_preset:
                st.session_state["active_investigation_id"] = single_key_preset

            render_export_to_tracker(
                tracker_inst=tracker,
                content_to_export=st.session_state["jira_result"],
                default_summary=f"Jira Analysis: {single_key_preset or jira_action}",
                observation_text=f"Jira {jira_action} findings:\n"
                + st.session_state["jira_result"][:500],
                key_prefix="jira_search",
                selected_model=selected_global_model,
                system_name="Jira",
            )

# -----------------------------------------------------------------------------
# TAB: PAGERDUTY ALERTS & INCIDENTS (tools/pagerduty_tool.py)
# -----------------------------------------------------------------------------
with tab_pagerduty:
    st.subheader("🚨 PagerDuty Alert Inspection & Incident Triage")
    st.caption(
        "Correlate real-time PagerDuty alerts, raw payloads, and metrics with Jira tickets and investigations."
    )

    from tools.pagerduty_tool import (
        list_incidents,
        get_incident_alerts,
        get_incident_log_entries,
        format_incident_markdown,
        get_pd_config,
    )

    pd_api_key, pd_base_url = get_pd_config()
    if not pd_api_key:
        st.warning(
            "⚠️ PagerDuty API key not detected. Set `PAGERDUTY_API_TOKEN` in `.env` or your shell environment to fetch alerts."
        )

    col_pd_left, col_pd_right = st.columns([1, 2])

    with col_pd_left:
        st.markdown("#### 🔍 Filter Incidents")
        pd_status_filter = st.multiselect(
            "Incident Status",
            options=["triggered", "acknowledged", "resolved"],
            default=["triggered", "acknowledged"],
            help="Filter by incident lifecycle status.",
        )
        pd_urgency_filter = st.selectbox(
            "Urgency", options=["all", "high", "low"], index=0
        )
        pd_query = st.text_input(
            "Search / Keyword", placeholder="e.g. host, service, or cluster name"
        )
        pd_limit = st.slider("Limit", min_value=5, max_value=50, value=20, step=5)

        if st.button(
            "Fetch PagerDuty Incidents", type="primary", use_container_width=True
        ):
            if not pd_api_key:
                st.error("Cannot fetch: PAGERDUTY_API_TOKEN is missing.")
            else:
                with st.spinner("Querying PagerDuty API..."):
                    try:
                        incidents_data = list_incidents(
                            statuses=pd_status_filter,
                            urgency=pd_urgency_filter,
                            query=pd_query,
                            limit=pd_limit,
                        )
                        st.session_state["pd_incidents_list"] = incidents_data
                        if not incidents_data:
                            st.info("No matching incidents found.")
                    except Exception as err:
                        st.error(f"Failed to fetch incidents: {err}")

        # Show incident selector if fetched
        fetched_incidents = st.session_state.get("pd_incidents_list", [])
        selected_inc = None
        if fetched_incidents:
            incident_labels = {
                f"{'🔴' if inc.get('status') == 'triggered' else '🟡' if inc.get('status') == 'acknowledged' else '🟢'} #{inc.get('incident_number')}: {inc.get('title', 'Untitled')[:40]} ({inc.get('service', {}).get('summary', 'N/A')})": inc
                for inc in fetched_incidents
            }
            chosen_label = st.selectbox(
                "Select Incident to Inspect", list(incident_labels.keys())
            )
            selected_inc = incident_labels[chosen_label]

    with col_pd_right:
        if selected_inc:
            inc_id = selected_inc.get("id")
            inc_num = selected_inc.get("incident_number")
            inc_title = selected_inc.get("title") or selected_inc.get("summary", "")
            inc_status = selected_inc.get("status", "").upper()
            inc_urgency = selected_inc.get("urgency", "").upper()
            svc_name = (selected_inc.get("service") or {}).get("summary", "N/A")
            created_at = selected_inc.get("created_at", "N/A")
            pd_url = selected_inc.get("html_url", "")

            status_color = (
                "#e53e3e"
                if inc_status == "TRIGGERED"
                else "#dd6b20"
                if inc_status == "ACKNOWLEDGED"
                else "#38a169"
            )
            st.markdown(
                f"""
                <div style="padding: 14px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.15); background-color: rgba(28, 31, 38, 0.6); margin-bottom: 15px;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-size: 1.1rem; font-weight: bold;">Incident #{inc_num}: {inc_title}</span>
                        <span style="background-color: {status_color}; padding: 3px 10px; border-radius: 12px; font-size: 0.8rem; font-weight: bold;">{inc_status}</span>
                    </div>
                    <div style="font-size: 0.85rem; color: #a0aec0; margin-top: 6px;">
                        <strong>Service:</strong> {svc_name} &nbsp;|&nbsp; <strong>Urgency:</strong> {inc_urgency} &nbsp;|&nbsp; <strong>Created:</strong> {created_at}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if pd_url:
                st.caption(f"🔗 [Open in PagerDuty Web Console]({pd_url})")

            with st.spinner("Fetching alert payload & logs..."):
                try:
                    alerts = get_incident_alerts(inc_id)
                    logs = get_incident_log_entries(inc_id)
                    incident_md = format_incident_markdown(
                        selected_inc, alerts=alerts, logs=logs
                    )
                except Exception as err:
                    st.error(f"Error fetching alerts/logs: {err}")
                    alerts = []
                    logs = []
                    incident_md = format_incident_markdown(selected_inc)

            det_t1, det_t2, det_t3 = st.tabs(
                ["📊 Alert Details & Payload", "📜 Timeline Log", "📝 Markdown Digest"]
            )
            with det_t1:
                if alerts:
                    for i, al in enumerate(alerts, 1):
                        al_summary = al.get("summary", "Alert")
                        al_sev = al.get("severity", "unknown").upper()
                        al_body = (al.get("body") or {}).get("details", {})
                        with st.expander(
                            f"Alert #{i}: [{al_sev}] {al_summary}", expanded=True
                        ):
                            if al_body:
                                st.json(al_body)
                            else:
                                st.write("No raw details in alert body.")
                else:
                    st.info("No separate alert payloads returned for this incident.")

            with det_t2:
                if logs:
                    for le in logs:
                        l_time = le.get("created_at", "")
                        l_agent = (le.get("agent") or {}).get("summary", "System")
                        l_summary = le.get("summary") or le.get("type", "Log")
                        st.markdown(f"- `{l_time}` **[{l_agent}]** {l_summary}")
                else:
                    st.info("No timeline logs found.")

            with det_t3:
                st.code(incident_md, language="markdown")

            st.divider()
            st.markdown("#### ⚡ Correlate & Push Actions")

            act_col1, act_col2 = st.columns([1, 1])

            with act_col1:
                st.markdown("**🤖 AI Incident Triage & Correlation**")
                pd_prompt = st.text_input(
                    "Optional Correlation Prompt",
                    placeholder="e.g. Correlate with SPRE Jira tickets or recommend check commands",
                    key="pd_custom_prompt",
                )
                if st.button(
                    "Run AI Triage on Incident",
                    type="primary",
                    use_container_width=True,
                ):
                    with st.spinner("Synthesizing incident context with LLM..."):
                        triage_output = LLMBridge.synthesize(
                            system_prompt=(
                                "You are a Senior SRE performing rapid incident triaging, root cause isolation, "
                                "and correlation with Jira/infrastructure repositories.\n"
                                "Provide:\n"
                                "1. Incident & Symptom Summary\n"
                                "2. Probable Root Cause Indicators & Extracted Alert Entities\n"
                                "3. Correlated Investigation / Jira Suggestions (Ticket title & tags)\n"
                                "4. Immediate Verification / Diagnostic Commands (run-ready bash/oc/kubectl commands)\n"
                            ),
                            user_payload=f"{incident_md}\n\nUser Question/Focus: {pd_prompt}"
                            if pd_prompt
                            else incident_md,
                            model=selected_global_model,
                        )
                        st.session_state[
                            "pagerduty_result"
                        ] = f"{incident_md}\n\n### AI TRIAGE & CORRELATION:\n{triage_output}"
                        st.success("Triage completed! Staged for OpenCode Exporter.")
                        st.markdown(triage_output)

            with act_col2:
                st.markdown("**🎯 Export to Investigation Tracker (Jira-Centric)**")
                st.caption(
                    "All incident data is consolidated into a single Jira ticket investigation file."
                )

                existing_jira_invs = tracker.list_investigations()
                jira_target_choice = st.radio(
                    "Target Jira Ticket",
                    ["Enter Jira Issue Key", "Select Existing Investigation"]
                    if existing_jira_invs
                    else ["Enter Jira Issue Key"],
                    key="pd_jira_target_choice",
                    horizontal=True,
                )

                if (
                    jira_target_choice == "Select Existing Investigation"
                    and existing_jira_invs
                ):
                    chosen_jira_key = st.selectbox(
                        "Existing Jira Investigation",
                        existing_jira_invs,
                        key="pd_chosen_jira_key",
                    )
                else:
                    chosen_jira_key = (
                        st.text_input(
                            "Jira Issue Key",
                            value=st.session_state.get("active_investigation_id", ""),
                            placeholder="e.g. SPRE-5115 or KFLUXSPRT-1234",
                            key="pd_input_jira_key",
                        )
                        .strip()
                        .upper()
                    )

                if st.button(
                    "📤 Export to Investigation Tracker",
                    type="primary",
                    use_container_width=True,
                ):
                    if not chosen_jira_key:
                        st.warning(
                            "Please provide a valid Jira Issue Key (e.g. SPRE-5115)."
                        )
                    else:
                        is_existing = tracker.exists(chosen_jira_key)
                        alert_fact = (
                            f"PagerDuty Incident #{inc_num} [{inc_status} / {inc_urgency}]: '{inc_title}' on service '{svc_name}'. "
                            f"Created at: {created_at}. Web Link: {pd_url}"
                        )

                        # Save PagerDuty artifact into the ticket's folder
                        tracker.save_artifact(
                            chosen_jira_key,
                            f"pagerduty_incident_{inc_num}.md",
                            incident_md,
                        )

                        if not is_existing:
                            new_content = tracker.create_investigation(
                                incident_id=chosen_jira_key,
                                title=f"Incident triage for {svc_name}: {inc_title}",
                                target_system=svc_name,
                                initial_fact=alert_fact,
                                initial_hypothesis=f"Service '{svc_name}' triggered alert: {inc_title}. Check logs and metric anomalies.",
                            )
                            st.session_state[
                                "active_investigation_id"
                            ] = chosen_jira_key
                            st.session_state["active_investigation_doc"] = new_content
                            st.session_state["pagerduty_result"] = incident_md
                            st.success(
                                f"✅ Initialized directory `{chosen_jira_key}/`, saved artifact `pagerduty_incident_{inc_num}.md`, and created `analysis.md`!"
                            )
                        else:
                            with st.spinner(
                                f"Updating `{chosen_jira_key}/analysis.md` via {selected_global_model}..."
                            ):
                                updated_doc = tracker.update_with_ai(
                                    incident_id=chosen_jira_key,
                                    observation=f"Correlated PagerDuty Incident #{inc_num}:\n{alert_fact}",
                                    model=selected_global_model,
                                    context_attachment=incident_md,
                                )
                                st.session_state[
                                    "active_investigation_id"
                                ] = chosen_jira_key
                                st.session_state[
                                    "active_investigation_doc"
                                ] = updated_doc
                                st.session_state["pagerduty_result"] = incident_md
                                st.success(
                                    f"✅ Saved artifact `pagerduty_incident_{inc_num}.md` and updated `{chosen_jira_key}/analysis.md`!"
                                )
        else:
            st.info(
                "👈 Use the left panel to fetch and select a PagerDuty incident to inspect."
            )

# -----------------------------------------------------------------------------
# TAB 5: GITLAB MR/PIPELINE (tools/gitlab_analyze.py)
# -----------------------------------------------------------------------------
with tab_gitlab:
    st.subheader("GitLab MR & Pipeline Review")

    col_g1, col_g2 = st.columns([1, 1])

    with col_g1:
        gitlab_target = st.text_input(
            "GitLab Target (MR URL, Pipeline URL, or Local Repo Path)",
            placeholder=f"{GITLAB_URL}/group/repo/-/merge_requests/102",
        )
        gitlab_prompt = st.text_area(
            "Instruction / Question",
            value="Review this MR and highlight any potential risks or breaking changes.",
        )

        if st.button("Run GitLab Analysis", type="primary", use_container_width=True):
            if not gitlab_target:
                st.warning("Please enter a valid GitLab MR/Pipeline URL or path.")
            else:
                with st.spinner("Analyzing GitLab resource..."):
                    out = SREExternalTools.run_gitlab_analyze(
                        target=gitlab_target, prompt=gitlab_prompt
                    )
                    st.session_state["gitlab_result"] = out

    with col_g2:
        if st.session_state["gitlab_result"]:
            st.markdown("### GitLab Review Result")
            st.markdown(st.session_state["gitlab_result"])

            render_export_to_tracker(
                tracker_inst=tracker,
                content_to_export=st.session_state["gitlab_result"],
                default_summary=f"GitLab Review: {gitlab_target[:40]}",
                observation_text="GitLab review findings:\n"
                + st.session_state["gitlab_result"][:500],
                key_prefix="gitlab",
                selected_model=selected_global_model,
                system_name="GitLab",
            )

# -----------------------------------------------------------------------------
# TAB 9: OPENSHIFT DIAGNOSTICS
# -----------------------------------------------------------------------------
with tab_openshift:
    st.header("☸️ OpenShift Diagnostics")
    st.markdown(
        "Diagnose failing pods and extract sanitized error logs without downloading gigabytes of text."
    )

    os_col1, os_col2 = st.columns([1, 2])
    with os_col1:
        os_ns = st.text_input(
            "Namespace (OpenShift)", value="my-tenant-namespace", key="os_ns"
        )
        os_action = st.radio(
            "Action", ["List Failing Pods", "Diagnose Specific Pod"], key="os_action"
        )
        os_pod_name = ""
        if os_action == "Diagnose Specific Pod":
            os_pod_name = st.text_input("Pod Name", key="os_pod")

        if st.button("Run OpenShift Tool", type="primary"):
            with st.spinner("Executing OpenShift diagnostics..."):
                if os_action == "List Failing Pods":
                    res = SREExternalTools.run_openshift_pods(os_ns)
                    st.session_state["os_result"] = res
                elif os_action == "Diagnose Specific Pod" and os_pod_name:
                    res = SREExternalTools.run_openshift_diagnose(os_pod_name, os_ns)
                    st.session_state["os_result"] = res

    with os_col2:
        if "os_result" in st.session_state:
            st.code(st.session_state["os_result"], language="text")

# -----------------------------------------------------------------------------
# TAB 10: TEKTON / KONFLUX ANALYZER
# -----------------------------------------------------------------------------
with tab_tekton:
    st.header("🐙 Tekton / Konflux CI/CD Analyzer")
    st.markdown(
        "Find the exact failing step in a PipelineRun and extract its sanitized logs."
    )

    tkn_col1, tkn_col2 = st.columns([1, 2])
    with tkn_col1:
        tkn_ns = st.text_input(
            "Namespace (Tekton)", value="my-tenant-namespace", key="tkn_ns"
        )
        tkn_action = st.radio(
            "Action",
            ["List Failed PipelineRuns", "Diagnose PipelineRun", "Diagnose TaskRun"],
            key="tkn_action",
        )
        tkn_target_name = ""

        if tkn_action != "List Failed PipelineRuns":
            tkn_target_name = st.text_input(
                "PipelineRun / TaskRun Name", key="tkn_name"
            )

        if st.button("Run Tekton Tool", type="primary"):
            with st.spinner("Analyzing Tekton pipelines..."):
                if tkn_action == "List Failed PipelineRuns":
                    res = SREExternalTools.run_tekton_list(tkn_ns)
                    st.session_state["tkn_result"] = res
                elif tkn_action == "Diagnose PipelineRun" and tkn_target_name:
                    res = SREExternalTools.run_tekton_diagnose_pr(
                        tkn_target_name, tkn_ns
                    )
                    st.session_state["tkn_result"] = res
                elif tkn_action == "Diagnose TaskRun" and tkn_target_name:
                    res = SREExternalTools.run_tekton_diagnose_tr(
                        tkn_target_name, tkn_ns
                    )
                    st.session_state["tkn_result"] = res

    with tkn_col2:
        if "tkn_result" in st.session_state:
            st.code(st.session_state["tkn_result"], language="text")

# -----------------------------------------------------------------------------
# TAB 11: OPENCODE CONTEXT EXPORTER
# -----------------------------------------------------------------------------
with tab_export:
    st.subheader("Stateless OpenCode Session Bundle")
    st.caption(
        "Copy this payload directly into a fresh, empty OpenCode chat. Zero context history, zero token waste."
    )

    bundle_parts = []
    if st.session_state.get("active_investigation_doc"):
        bundle_parts.append(
            "### ACTIVE INVESTIGATION (TIMELINE & HYPOTHESES):\n"
            + st.session_state["active_investigation_doc"]
        )
    if st.session_state.get("generated_map"):
        bundle_parts.append(
            "### ARCHITECTURE MAP:\n" + st.session_state["generated_map"]
        )
    if st.session_state.get("sanitized_error"):
        bundle_parts.append(
            "### ISOLATED ERROR CORE:\n" + st.session_state["sanitized_error"]
        )
    if st.session_state.get("slack_result"):
        bundle_parts.append(
            "### SLACK CONTEXT DIGEST:\n" + st.session_state["slack_result"]
        )
    if st.session_state.get("jira_result"):
        bundle_parts.append(
            "### JIRA TICKETS DIGEST:\n" + st.session_state["jira_result"]
        )
    if st.session_state.get("pagerduty_result"):
        bundle_parts.append(
            "### PAGERDUTY INCIDENT CONTEXT:\n" + st.session_state["pagerduty_result"]
        )
    if st.session_state.get("gitlab_result"):
        bundle_parts.append(
            "### GITLAB CONTEXT DIGEST:\n" + st.session_state["gitlab_result"]
        )

    final_bundle = (
        "\n\n---\n\n".join(bundle_parts)
        if bundle_parts
        else "No context data currently generated."
    )

    st.code(final_bundle, language="markdown")

    if bundle_parts:
        st.divider()
        st.markdown("#### 💾 Save & Export Session Bundle")

        existing_invs = tracker.list_investigations()
        active_id = st.session_state.get("active_investigation_id", "")

        c_dest1, c_dest2 = st.columns([1, 1])
        with c_dest1:
            target_jira_key = st.text_input(
                "Target Jira Ticket Key",
                value=active_id,
                placeholder="e.g. SPRE-5115 or KFLUXSPRT-1234",
                help="Saves the bundle into the dedicated ticket directory in investigations/detailed/<KEY>/",
            )
        with c_dest2:
            bundle_filename = st.text_input(
                "Bundle Filename", value="opencode_session_context.md"
            )

        clean_key = target_jira_key.strip().upper()
        if clean_key:
            target_dir = tracker.get_ticket_dir(clean_key)
            save_file = target_dir / bundle_filename
            st.caption(f"📁 Destination path: `{save_file}`")
        else:
            save_file = BUNDLES_DIR / bundle_filename
            st.caption(
                f"📁 Fallback path: `{save_file}` (Enter a Jira Key above to save to ticket directory)"
            )

        c_act1, c_act2 = st.columns(2)
        with c_act1:
            btn_label = (
                f"💾 Save Bundle to {clean_key}/"
                if clean_key
                else "💾 Save Bundle to state/bundles/"
            )
            if st.button(btn_label, type="primary", use_container_width=True):
                save_file.parent.mkdir(parents=True, exist_ok=True)
                save_file.write_text(final_bundle, encoding="utf-8")
                st.success(f"✅ Saved bundle to `{save_file}`!")
                if clean_key:
                    st.session_state["active_investigation_id"] = clean_key
                    st.rerun()

        with c_act2:
            st.download_button(
                label="📥 Download Bundle (.md)",
                data=final_bundle,
                file_name=bundle_filename,
                mime="text/markdown",
                use_container_width=True,
            )

        if clean_key:
            st.markdown(
                f"""
                <div style="margin-top: 10px; background-color: rgba(33, 40, 54, 0.6); padding: 12px 16px; border-radius: 6px; border-left: 4px solid #3182ce;">
                    <span style="color: #63b3ed; font-weight: 600;">🚀 Run in OpenCode CLI:</span>
                    <pre style="margin-top: 6px; padding: 8px; background: rgba(0,0,0,0.3); border-radius: 4px;">opencode "{save_file}"</pre>
                </div>
                """,
                unsafe_allow_html=True,
            )
