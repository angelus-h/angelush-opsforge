import argparse
import json
import subprocess
import sys
import os
from pathlib import Path
from typing import Dict, List, Optional

# Add parent directory to path to allow importing from core
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.log_sanitizer import LogSanitizer

def run_command(cmd: List[str]) -> str:
    """Runs a CLI command and returns its standard output. Raises an error on failure."""
    try:
        result = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error running command {' '.join(cmd)}: {e.stderr}", file=sys.stderr)
        raise

def get_failing_pods(namespace: str, failing_only: bool = True) -> List[Dict]:
    """Returns pods with status != Running/Completed or restartCount > 0."""
    cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", "json"]
    try:
        output = run_command(cmd)
        data = json.loads(output)
        
        pods = data.get("items", [])
        if not failing_only:
            return pods
            
        failing_pods = []
        for pod in pods:
            status = pod.get("status", {})
            phase = status.get("phase", "")
            
            # Check for non-standard phases
            is_failing = phase not in ["Running", "Succeeded"]
            
            # Check container statuses for CrashLoopBackOff, Error, etc. or restarts
            container_statuses = status.get("containerStatuses", [])
            for cs in container_statuses:
                state = cs.get("state", {})
                if "waiting" in state and state["waiting"].get("reason") in ["CrashLoopBackOff", "ImagePullBackOff", "CreateContainerConfigError", "ErrImagePull"]:
                    is_failing = True
                if "terminated" in state and state["terminated"].get("exitCode", 0) != 0:
                    is_failing = True
                if cs.get("restartCount", 0) > 0:
                    is_failing = True
                    
            if is_failing:
                failing_pods.append(pod)
                
        return failing_pods
    except Exception as e:
        print(f"Failed to fetch pods in namespace {namespace}: {e}")
        return []

def extract_pod_failure_core(pod_name: str, namespace: str, tail_lines: int = 100) -> Dict:
    """
    Extracts exit code, termination reason, and last sanitized log lines 
    from the crashing container.
    """
    cmd = ["kubectl", "get", "pod", pod_name, "-n", namespace, "-o", "json"]
    try:
        output = run_command(cmd)
        pod = json.loads(output)
        
        status = pod.get("status", {})
        container_statuses = status.get("containerStatuses", [])
        
        failed_containers = []
        
        # Identify failing containers
        for cs in container_statuses:
            container_name = cs.get("name")
            state = cs.get("state", {})
            
            is_failing = False
            reason = ""
            exit_code = 0
            
            if "waiting" in state:
                reason = state["waiting"].get("reason", "Waiting")
                if reason in ["CrashLoopBackOff", "ImagePullBackOff", "CreateContainerConfigError", "ErrImagePull"]:
                    is_failing = True
            elif "terminated" in state:
                reason = state["terminated"].get("reason", "Terminated")
                exit_code = state["terminated"].get("exitCode", 0)
                if exit_code != 0:
                    is_failing = True
                    
            if is_failing or cs.get("restartCount", 0) > 0:
                # Fetch logs for this container
                log_cmd = ["kubectl", "logs", pod_name, "-c", container_name, "-n", namespace, f"--tail={tail_lines}"]
                # For CrashLoopBackOff, we might need --previous to see why it crashed
                prev_log_cmd = ["kubectl", "logs", pod_name, "-c", container_name, "-n", namespace, "--previous", f"--tail={tail_lines}"]
                
                raw_logs = ""
                try:
                    raw_logs = run_command(log_cmd)
                except subprocess.CalledProcessError:
                    try:
                        raw_logs = run_command(prev_log_cmd)
                    except subprocess.CalledProcessError:
                        raw_logs = "Could not fetch logs (neither current nor previous)."
                
                sanitized_log = LogSanitizer.extract_error_core(raw_logs, context_lines=5, max_total_lines=150)
                
                failed_containers.append({
                    "container_name": container_name,
                    "reason": reason,
                    "exit_code": exit_code,
                    "restarts": cs.get("restartCount", 0),
                    "sanitized_log": sanitized_log
                })
                
        return {
            "pod_name": pod_name,
            "namespace": namespace,
            "phase": status.get("phase"),
            "failed_containers": failed_containers
        }

    except Exception as e:
        print(f"Failed to extract failure core for pod {pod_name}: {e}")
        return {"error": str(e)}

def get_recent_warning_events(namespace: str, minutes: int = 30) -> List[Dict]:
    """Returns deduplicated warning/failed events."""
    cmd = ["kubectl", "get", "events", "-n", namespace, "--field-selector", "type!=Normal", "-o", "json"]
    try:
        output = run_command(cmd)
        data = json.loads(output)
        
        events = data.get("items", [])
        warning_events = []
        
        # Simple deduplication based on reason and object
        seen = set()
        
        for event in events:
            reason = event.get("reason", "Unknown")
            message = event.get("message", "")
            involved_obj = event.get("involvedObject", {})
            obj_kind = involved_obj.get("kind", "Unknown")
            obj_name = involved_obj.get("name", "Unknown")
            count = event.get("count", 1)
            last_timestamp = event.get("lastTimestamp") or event.get("eventTime")
            
            dedup_key = f"{reason}:{obj_kind}:{obj_name}"
            
            if dedup_key not in seen:
                seen.add(dedup_key)
                warning_events.append({
                    "reason": reason,
                    "message": message,
                    "object": f"{obj_kind}/{obj_name}",
                    "count": count,
                    "last_seen": last_timestamp
                })
                
        return warning_events
    except Exception as e:
        print(f"Failed to fetch events in namespace {namespace}: {e}")
        return []

def main():
    parser = argparse.ArgumentParser(description="OpenShift/Kubernetes Diagnostics Tool (Spec 003)")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command: pods
    pods_parser = subparsers.add_parser("pods", help="List failing pods in a namespace")
    pods_parser.add_argument("--namespace", required=True, help="Target namespace")
    pods_parser.add_argument("--failing-only", action="store_true", default=True, help="Only show failing pods (default: True)")
    pods_parser.add_argument("--all", action="store_false", dest="failing_only", help="Show all pods, not just failing ones")

    # Command: diagnose
    diag_parser = subparsers.add_parser("diagnose", help="Diagnose a specific failing pod")
    diag_parser.add_argument("pod_name", help="Name of the pod to diagnose")
    diag_parser.add_argument("--namespace", required=True, help="Target namespace")
    diag_parser.add_argument("--jira", help="Optional Jira issue key to attach the diagnosis (not implemented yet)")
    diag_parser.add_argument("--tail", type=int, default=100, help="Number of log lines to tail (default: 100)")

    # Command: events
    events_parser = subparsers.add_parser("events", help="List warning events in a namespace")
    events_parser.add_argument("--namespace", required=True, help="Target namespace")

    args = parser.parse_args()

    if args.command == "pods":
        print(f"Fetching {'failing ' if args.failing_only else ''}pods in namespace: {args.namespace}...")
        pods = get_failing_pods(args.namespace, failing_only=args.failing_only)
        if not pods:
            print("No pods found matching criteria.")
        else:
            for pod in pods:
                name = pod.get("metadata", {}).get("name")
                phase = pod.get("status", {}).get("phase")
                print(f"- {name} (Phase: {phase})")
                
                # Try to print some context about why it's failing
                for cs in pod.get("status", {}).get("containerStatuses", []):
                    state = cs.get("state", {})
                    if "waiting" in state:
                        print(f"  Container '{cs.get('name')}': Waiting ({state['waiting'].get('reason')})")
                    elif "terminated" in state and state["terminated"].get("exitCode", 0) != 0:
                        print(f"  Container '{cs.get('name')}': Terminated (Exit: {state['terminated'].get('exitCode')}, Reason: {state['terminated'].get('reason')})")
                    elif cs.get("restartCount", 0) > 0:
                        print(f"  Container '{cs.get('name')}': Restarts: {cs.get('restartCount')}")
                        
    elif args.command == "diagnose":
        print(f"Diagnosing pod: {args.pod_name} in namespace: {args.namespace}...")
        result = extract_pod_failure_core(args.pod_name, args.namespace, tail_lines=args.tail)
        
        if "error" in result:
            print(f"Error: {result['error']}")
            sys.exit(1)
            
        print(f"\nPod: {result['pod_name']} (Phase: {result['phase']})")
        print("="*40)
        
        if not result.get("failed_containers"):
            print("No failed containers detected or logs could not be retrieved.")
            
        for container in result.get("failed_containers", []):
            print(f"\nContainer: {container['container_name']}")
            print(f"Reason: {container['reason']}")
            if container['exit_code'] != 0:
                print(f"Exit Code: {container['exit_code']}")
            if container['restarts'] > 0:
                print(f"Restarts: {container['restarts']}")
            
            print("\n--- Sanitized Logs ---")
            print(container['sanitized_log'])
            print("----------------------\n")
            
        if args.jira:
            print(f"Note: Jira attachment functionality for {args.jira} will be implemented in the integration phase.")

    elif args.command == "events":
        print(f"Fetching warning events in namespace: {args.namespace}...")
        events = get_recent_warning_events(args.namespace)
        if not events:
            print("No warning events found.")
        else:
            for event in events:
                print(f"\n[{event['last_seen']}] {event['reason']} on {event['object']} (x{event['count']})")
                print(f"Message: {event['message']}")

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
