import subprocess
from pathlib import Path

class SREExternalTools:
    """Invokes self-contained local tools housed in the tools/ directory."""

    TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
    SLACK_SCRIPT = TOOLS_DIR / "slack_analyze.py"
    JIRA_SCRIPT = TOOLS_DIR / "jira_gemini.py"
    GITLAB_SCRIPT = TOOLS_DIR / "gitlab_analyze.py"
    PAGERDUTY_SCRIPT = TOOLS_DIR / "pagerduty_tool.py"
    OPENSHIFT_SCRIPT = TOOLS_DIR / "openshift_tool.py"
    TEKTON_SCRIPT = TOOLS_DIR / "tekton_tool.py"

    @classmethod
    def run_openshift_pods(cls, namespace: str, failing_only: bool = True) -> str:
        if not cls.OPENSHIFT_SCRIPT.exists():
            return f"Error: OpenShift tool not found at {cls.OPENSHIFT_SCRIPT}"
            
        cmd = ["python3", str(cls.OPENSHIFT_SCRIPT), "pods", "--namespace", namespace]
        if not failing_only:
            cmd.append("--all")
            
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"OpenShift pods error:\n{e.stderr or e.stdout}"
            
    @classmethod
    def run_openshift_diagnose(cls, pod_name: str, namespace: str, tail: int = 100) -> str:
        if not cls.OPENSHIFT_SCRIPT.exists():
            return f"Error: OpenShift tool not found at {cls.OPENSHIFT_SCRIPT}"
            
        cmd = ["python3", str(cls.OPENSHIFT_SCRIPT), "diagnose", pod_name, "--namespace", namespace, "--tail", str(tail)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"OpenShift diagnose error:\n{e.stderr or e.stdout}"

    @classmethod
    def run_tekton_list(cls, namespace: str, limit: int = 10) -> str:
        if not cls.TEKTON_SCRIPT.exists():
            return f"Error: Tekton tool not found at {cls.TEKTON_SCRIPT}"
            
        cmd = ["python3", str(cls.TEKTON_SCRIPT), "list-failed", "--namespace", namespace, "--limit", str(limit)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"Tekton list error:\n{e.stderr or e.stdout}"

    @classmethod
    def run_tekton_diagnose_pr(cls, pr_name: str, namespace: str) -> str:
        if not cls.TEKTON_SCRIPT.exists():
            return f"Error: Tekton tool not found at {cls.TEKTON_SCRIPT}"
            
        cmd = ["python3", str(cls.TEKTON_SCRIPT), "pipelinerun", pr_name, "--namespace", namespace]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"Tekton PipelineRun diagnose error:\n{e.stderr or e.stdout}"

    @classmethod
    def run_tekton_diagnose_tr(cls, tr_name: str, namespace: str) -> str:
        if not cls.TEKTON_SCRIPT.exists():
            return f"Error: Tekton tool not found at {cls.TEKTON_SCRIPT}"
            
        cmd = ["python3", str(cls.TEKTON_SCRIPT), "taskrun", tr_name, "--namespace", namespace]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"Tekton TaskRun diagnose error:\n{e.stderr or e.stdout}"

    @classmethod
    def run_slack_analyze(cls, target: str, prompt: str = "", limit: int = 50, search: str = "") -> str:
        if not cls.SLACK_SCRIPT.exists():
            return f"Error: Slack tool not found at {cls.SLACK_SCRIPT}"

        cmd = ["python3", str(cls.SLACK_SCRIPT), target]
        if prompt:
            cmd.append(prompt)
        if limit:
            cmd.extend(["--limit", str(limit)])
        if search:
            cmd.extend(["--search", search])

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"Slack analysis error (code {e.returncode}):\n{e.stderr or e.stdout}"

    @classmethod
    def run_jira_command(cls, mode: str = "summarize", target: str = "") -> str:
        if not cls.JIRA_SCRIPT.exists():
            return f"Error: Jira tool not found at {cls.JIRA_SCRIPT}"

        cmd = ["python3", str(cls.JIRA_SCRIPT), mode]
        if target:
            cmd.append(target)

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"Jira tool error (code {e.returncode}):\n{e.stderr or e.stdout}"

    @classmethod
    def run_gitlab_analyze(cls, target: str, prompt: str = "") -> str:
        if not cls.GITLAB_SCRIPT.exists():
            return f"Error: GitLab tool not found at {cls.GITLAB_SCRIPT}"

        cmd = ["python3", str(cls.GITLAB_SCRIPT), target]
        if prompt:
            cmd.append(prompt)

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"GitLab tool error (code {e.returncode}):\n{e.stderr or e.stdout}"

    @classmethod
    def run_pagerduty_command(cls, command: str, *args) -> str:
        if not cls.PAGERDUTY_SCRIPT.exists():
            return f"Error: PagerDuty tool not found at {cls.PAGERDUTY_SCRIPT}"

        cmd = ["python3", str(cls.PAGERDUTY_SCRIPT), command, *[str(a) for a in args if str(a)]]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"PagerDuty tool error (code {e.returncode}):\n{e.stderr or e.stdout}"
