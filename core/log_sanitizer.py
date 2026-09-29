import os
import re
import base64
import ssl
import urllib.request
import urllib.error
from typing import List, Optional


class LogSanitizer:
    """Strips terminal escape sequences and non-essential lines, extracting only error cores."""

    ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

    # Enhanced keywords including Jenkins, OpenShift/K8s, Ansible, Python, Java stack traces
    ERROR_KEYWORDS = re.compile(
        r"("
        r"failed|fatal|error|traceback|exception|panic|denied|timed out|refused|"
        r"hudson\.AbortException|org\.jenkinsci|script returned exit code [1-9]|"
        r"Build step .* marked build as failure|FAILED! =>|"
        r"CrashLoopBackOff|ImagePullBackOff|OOMKilled|Error from server"
        r")",
        re.IGNORECASE,
    )

    @classmethod
    def strip_ansi(cls, text: str) -> str:
        return cls.ANSI_ESCAPE.sub("", text)

    @classmethod
    def extract_error_core(
        cls, raw_log: str, context_lines: int = 4, max_total_lines: int = 150
    ) -> str:
        clean = cls.strip_ansi(raw_log)
        lines = clean.splitlines()

        if len(lines) <= max_total_lines:
            return clean

        error_indices = set()
        for idx, line in enumerate(lines):
            if cls.ERROR_KEYWORDS.search(line):
                start = max(0, idx - context_lines)
                end = min(len(lines), idx + context_lines + 1)
                for i in range(start, end):
                    error_indices.add(i)

        if not error_indices:
            # Fallback: take the final 50 lines (where Jenkins and pipeline failures exit)
            return "\n".join(lines[-50:])

        sorted_indices = sorted(list(error_indices))
        selected_lines: List[str] = []
        last_idx = -1

        for idx in sorted_indices:
            if last_idx != -1 and idx > last_idx + 1:
                selected_lines.append("... [non-error logs truncated] ...")
            selected_lines.append(lines[idx])
            last_idx = idx

            if len(selected_lines) >= max_total_lines:
                selected_lines.append("... [output capped at max lines] ...")
                break

        return "\n".join(selected_lines)

    @classmethod
    def normalize_jenkins_url(cls, raw_url: str) -> str:
        """Normalizes various Jenkins URLs into a canonical .../consoleText URL."""
        url = raw_url.strip().rstrip("/")

        # Blue Ocean URL translation:
        # e.g., https://jenkins.../blue/organizations/jenkins/job-name/detail/job-name/42/pipeline
        bo_match = re.match(
            r"^(https?://[^/]+)/blue/organizations/jenkins/([^/]+)/detail/([^/]+)/(\d+)(?:/.*)?$",
            url,
        )
        if bo_match:
            base_host, _folder, job_name, build_num = bo_match.groups()
            return f"{base_host}/job/{job_name}/{build_num}/consoleText"

        # Trim typical console suffixes if user copied from browser URL bar
        suffixes_to_strip = [
            "/consoleText",
            "/consoleFull",
            "/console",
            "/pipeline-console",
            "/pipeline",
            "/logText/progressiveText",
            "/logText",
            "/log",
            "/display/redirect",
        ]
        for sfx in suffixes_to_strip:
            if url.endswith(sfx):
                url = url[: -len(sfx)]
                break

        url = url.rstrip("/")
        return f"{url}/consoleText"

    @classmethod
    def fetch_jenkins_log(
        cls, build_url: str, user: Optional[str] = None, token: Optional[str] = None
    ) -> str:
        """Fetches raw console text from a Jenkins build URL with authentication support."""
        if not build_url or not build_url.strip():
            return "Error: Empty Jenkins URL provided."

        clean_url = cls.normalize_jenkins_url(build_url)

        # Fallback to environment variables if credentials not explicitly passed
        active_user = (
            user or os.getenv("JENKINS_USER") or os.getenv("JENKINS_USERNAME") or ""
        ).strip()
        active_token = (
            token or os.getenv("JENKINS_TOKEN") or os.getenv("JENKINS_API_TOKEN") or ""
        ).strip()

        # If user is blank but email is present in env, default username to email local-part
        if not active_user and active_token:
            fallback_email = os.getenv("JIRA_EMAIL", "")
            if fallback_email and "@" in fallback_email:
                active_user = fallback_email.split("@")[0]

        req = urllib.request.Request(
            clean_url,
            headers={"User-Agent": "SRE-Hub/1.0", "Accept": "text/plain, */*"},
        )

        if active_token:
            if active_user:
                auth = base64.b64encode(
                    f"{active_user}:{active_token}".encode()
                ).decode()
                req.add_header("Authorization", f"Basic {auth}")
            else:
                # If only token is available, check if token itself is user:token, otherwise send Bearer
                if ":" in active_token:
                    auth = base64.b64encode(active_token.encode()).decode()
                    req.add_header("Authorization", f"Basic {auth}")
                else:
                    req.add_header("Authorization", f"Bearer {active_token}")

        # Setup SSL context that handles corporate internal CAs gracefully
        ssl_ctx = ssl.create_default_context()
        for ca_bundle in [
            "/etc/pki/tls/certs/ca-bundle.crt",
            "/etc/ssl/certs/ca-certificates.crt",
        ]:
            if os.path.exists(ca_bundle):
                try:
                    ssl_ctx.load_verify_locations(ca_bundle)
                    break
                except Exception:
                    pass

        try:
            with urllib.request.urlopen(req, timeout=35, context=ssl_ctx) as resp:
                # Read at most 10MB to prevent memory explosion on infinite logs
                raw_bytes = resp.read(10 * 1024 * 1024)
                content = raw_bytes.decode("utf-8", errors="replace")
                if not content.strip():
                    return f"Warning: Received empty console log from {clean_url}. The build may still be initializing or has no console output."
                return content
        except urllib.error.HTTPError as e:
            if e.code == 404:
                auth_hint = (
                    "Authenticated request"
                    if active_token
                    else "Unauthenticated request (No Jenkins Token provided)"
                )
                return (
                    f"Error: Jenkins returned HTTP 404 Not Found at `{clean_url}`.\n\n"
                    f"**Troubleshooting Check:**\n"
                    f"- Current auth status: {auth_hint}\n"
                    f"- **Important:** Many Jenkins instances intentionally return **HTTP 404** (instead of 403) for private/authenticated jobs when access is missing or credentials are not supplied.\n"
                    f"- Ensure your **Jenkins Username** and **Jenkins API Token** are correctly set and have Read access to the job.\n"
                    f"- Verify the build number actually exists (e.g. check if the job URL in your browser is `.../job/<name>/<build>/`)."
                )
            elif e.code in (401, 403):
                return (
                    f"Error: Jenkins returned HTTP {e.code} ({e.reason}) at `{clean_url}`.\n"
                    "Authentication failed. Please verify your Jenkins Username and API Token."
                )
            return f"Error fetching Jenkins console (HTTP {e.code}): {e.reason} at `{clean_url}`"
        except urllib.error.URLError as e:
            return f"Network error connecting to Jenkins (`{clean_url}`): {e.reason}"
        except Exception as e:
            return f"Unexpected error while fetching Jenkins log: {str(e)}"
