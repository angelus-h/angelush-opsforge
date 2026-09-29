import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

# Add parent directory to path to allow importing from core
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.log_sanitizer import LogSanitizer


def run_command(cmd: List[str]) -> str:
    """Runs a CLI command and returns its standard output. Raises an error on failure."""
    try:
        result = subprocess.run(
            cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error running command {' '.join(cmd)}: {e.stderr}", file=sys.stderr)
        raise


def get_failed_pipelineruns(namespace: str, limit: int = 10) -> List[Dict]:
    """Returns recently failed PipelineRuns in the namespace."""
    cmd = ["kubectl", "get", "pipelinerun", "-n", namespace, "-o", "json"]
    try:
        output = run_command(cmd)
        data = json.loads(output)

        runs = data.get("items", [])
        failed_runs = []

        for run in runs:
            status = run.get("status", {})
            conditions = status.get("conditions", [])

            is_failed = False
            for cond in conditions:
                if cond.get("type") == "Succeeded" and cond.get("status") == "False":
                    is_failed = True
                    break

            if is_failed:
                name = run.get("metadata", {}).get("name")
                start_time = status.get("startTime", "Unknown")
                completion_time = status.get("completionTime", "Unknown")
                failed_runs.append(
                    {
                        "name": name,
                        "start_time": start_time,
                        "completion_time": completion_time,
                    }
                )

        # Sort by completion time (newest first) and limit
        failed_runs.sort(key=lambda x: x["completion_time"], reverse=True)
        return failed_runs[:limit]

    except Exception as e:
        print(f"Failed to fetch PipelineRuns in namespace {namespace}: {e}")
        return []


def diagnose_pipelinerun(pr_name: str, namespace: str, tail_lines: int = 200) -> Dict:
    """
    Finds the failing TaskRun in a PipelineRun, and extracts the sanitized logs
    from the exact step that failed.
    """
    cmd = ["kubectl", "get", "pipelinerun", pr_name, "-n", namespace, "-o", "json"]
    try:
        output = run_command(cmd)
        pr = json.loads(output)

        status = pr.get("status", {})
        child_refs = status.get("childReferences", [])

        if not child_refs:
            return {"error": f"No child TaskRuns found for PipelineRun {pr_name}"}

        failed_taskruns = []

        # Iterate over child TaskRuns to find the failing one(s)
        for ref in child_refs:
            if ref.get("kind") != "TaskRun":
                continue

            tr_name = ref.get("name")
            tr_cmd = [
                "kubectl",
                "get",
                "taskrun",
                tr_name,
                "-n",
                namespace,
                "-o",
                "json",
            ]

            try:
                tr_output = run_command(tr_cmd)
                tr = json.loads(tr_output)

                tr_status = tr.get("status", {})
                tr_conditions = tr_status.get("conditions", [])

                is_failed = False
                for cond in tr_conditions:
                    if (
                        cond.get("type") == "Succeeded"
                        and cond.get("status") == "False"
                    ):
                        is_failed = True
                        break

                if not is_failed:
                    continue

                # TaskRun failed! Let's find the failing step (container)
                pod_name = tr_status.get("podName")
                if not pod_name:
                    failed_taskruns.append(
                        {
                            "taskrun": tr_name,
                            "error": "Pod name not found in TaskRun status. (Pod might be deleted)",
                        }
                    )
                    continue

                steps = tr_status.get("steps", [])
                failing_step = None

                for step in steps:
                    terminated = step.get("terminated", {})
                    if terminated and terminated.get("exitCode", 0) != 0:
                        failing_step = step
                        break

                if failing_step:
                    step_name = failing_step.get("name")
                    exit_code = failing_step.get("terminated", {}).get("exitCode")
                    reason = failing_step.get("terminated", {}).get("reason")

                    # Tekton container names are prefixed with 'step-'
                    container_name = f"step-{step_name}"

                    # Fetch logs for just this specific failing step
                    log_cmd = [
                        "kubectl",
                        "logs",
                        pod_name,
                        "-c",
                        container_name,
                        "-n",
                        namespace,
                        f"--tail={tail_lines}",
                    ]
                    raw_logs = "Could not fetch logs."

                    try:
                        raw_logs = run_command(log_cmd)
                    except subprocess.CalledProcessError:
                        # Try without step- prefix just in case
                        try:
                            log_cmd = [
                                "kubectl",
                                "logs",
                                pod_name,
                                "-c",
                                step_name,
                                "-n",
                                namespace,
                                f"--tail={tail_lines}",
                            ]
                            raw_logs = run_command(log_cmd)
                        except subprocess.CalledProcessError:
                            pass

                    sanitized_log = LogSanitizer.extract_error_core(
                        raw_logs, context_lines=10, max_total_lines=200
                    )

                    failed_taskruns.append(
                        {
                            "taskrun": tr_name,
                            "pod": pod_name,
                            "step": step_name,
                            "exit_code": exit_code,
                            "reason": reason,
                            "sanitized_log": sanitized_log,
                        }
                    )
                else:
                    failed_taskruns.append(
                        {
                            "taskrun": tr_name,
                            "error": "Could not identify a specific failing step (exitCode != 0).",
                        }
                    )

            except subprocess.CalledProcessError as e:
                failed_taskruns.append(
                    {
                        "taskrun": tr_name,
                        "error": f"Failed to fetch TaskRun details: {e}",
                    }
                )

        return {
            "pipelinerun": pr_name,
            "namespace": namespace,
            "failed_taskruns": failed_taskruns,
        }

    except Exception as e:
        return {"error": str(e)}


def diagnose_taskrun(tr_name: str, namespace: str, tail_lines: int = 200) -> Dict:
    """Diagnose a specific TaskRun directly."""
    # We can reuse the logic by pretending it's a child ref
    try:
        tr_cmd = ["kubectl", "get", "taskrun", tr_name, "-n", namespace, "-o", "json"]
        tr_output = run_command(tr_cmd)
        tr = json.loads(tr_output)

        tr_status = tr.get("status", {})
        pod_name = tr_status.get("podName")

        if not pod_name:
            return {"error": "Pod name not found in TaskRun status."}

        steps = tr_status.get("steps", [])
        failing_step = None

        for step in steps:
            terminated = step.get("terminated", {})
            if terminated and terminated.get("exitCode", 0) != 0:
                failing_step = step
                break

        if failing_step:
            step_name = failing_step.get("name")
            exit_code = failing_step.get("terminated", {}).get("exitCode")
            reason = failing_step.get("terminated", {}).get("reason")

            container_name = f"step-{step_name}"
            log_cmd = [
                "kubectl",
                "logs",
                pod_name,
                "-c",
                container_name,
                "-n",
                namespace,
                f"--tail={tail_lines}",
            ]

            try:
                raw_logs = run_command(log_cmd)
            except subprocess.CalledProcessError:
                try:
                    log_cmd = [
                        "kubectl",
                        "logs",
                        pod_name,
                        "-c",
                        step_name,
                        "-n",
                        namespace,
                        f"--tail={tail_lines}",
                    ]
                    raw_logs = run_command(log_cmd)
                except subprocess.CalledProcessError:
                    raw_logs = "Could not fetch logs."

            sanitized_log = LogSanitizer.extract_error_core(
                raw_logs, context_lines=10, max_total_lines=200
            )

            return {
                "taskrun": tr_name,
                "pod": pod_name,
                "step": step_name,
                "exit_code": exit_code,
                "reason": reason,
                "sanitized_log": sanitized_log,
            }
        else:
            return {"error": "No failing step found in TaskRun."}

    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="Tekton/Konflux CI/CD Pipeline Analyzer (Spec 004)"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command: list-failed
    list_parser = subparsers.add_parser(
        "list-failed", help="List recent failed PipelineRuns in a namespace"
    )
    list_parser.add_argument("--namespace", required=True, help="Target namespace")
    list_parser.add_argument(
        "--limit", type=int, default=10, help="Number of results to show"
    )

    # Command: pipelinerun
    pr_parser = subparsers.add_parser(
        "pipelinerun", help="Diagnose a failing PipelineRun"
    )
    pr_parser.add_argument("name", help="Name of the PipelineRun")
    pr_parser.add_argument("--namespace", required=True, help="Target namespace")
    pr_parser.add_argument(
        "--jira", help="Optional Jira issue key to attach the diagnosis"
    )

    # Command: taskrun
    tr_parser = subparsers.add_parser("taskrun", help="Diagnose a specific TaskRun")
    tr_parser.add_argument("name", help="Name of the TaskRun")
    tr_parser.add_argument("--namespace", required=True, help="Target namespace")

    args = parser.parse_args()

    if args.command == "list-failed":
        print(f"Fetching failed PipelineRuns in namespace: {args.namespace}...")
        runs = get_failed_pipelineruns(args.namespace, limit=args.limit)
        if not runs:
            print("No failed PipelineRuns found.")
        else:
            for run in runs:
                print(f"- {run['name']} (Completed: {run['completion_time']})")

    elif args.command == "pipelinerun":
        print(f"Diagnosing PipelineRun: {args.name} in namespace: {args.namespace}...")
        result = diagnose_pipelinerun(args.name, args.namespace)

        if "error" in result:
            print(f"Error: {result['error']}")
            sys.exit(1)

        print(f"\nPipelineRun: {result['pipelinerun']}")
        print("=" * 40)

        failed_trs = result.get("failed_taskruns", [])
        if not failed_trs:
            print("No failed TaskRuns found in this PipelineRun.")

        for tr in failed_trs:
            print(f"\nTaskRun: {tr.get('taskrun')}")
            if "error" in tr:
                print(f"Error analyzing TaskRun: {tr['error']}")
                continue

            print(f"Failing Step: {tr.get('step')}")
            print(f"Pod: {tr.get('pod')}")
            print(f"Exit Code: {tr.get('exit_code')} ({tr.get('reason')})")

            print("\n--- Sanitized Logs (Error Core) ---")
            print(tr.get("sanitized_log", "No logs extracted."))
            print("-----------------------------------\n")

        if args.jira:
            print(
                f"Note: Jira attachment functionality for {args.jira} will be implemented in the integration phase."
            )

    elif args.command == "taskrun":
        print(f"Diagnosing TaskRun: {args.name} in namespace: {args.namespace}...")
        result = diagnose_taskrun(args.name, args.namespace)

        if "error" in result:
            print(f"Error: {result['error']}")
            sys.exit(1)

        print(f"\nTaskRun: {result['taskrun']}")
        print("=" * 40)
        print(f"Failing Step: {result.get('step')}")
        print(f"Pod: {result.get('pod')}")
        print(f"Exit Code: {result.get('exit_code')} ({result.get('reason')})")

        print("\n--- Sanitized Logs (Error Core) ---")
        print(result.get("sanitized_log", "No logs extracted."))
        print("-----------------------------------\n")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
