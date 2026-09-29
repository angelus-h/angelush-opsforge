import subprocess
from pathlib import Path


class DiffSanitizer:
    """Extracts and sanitizes Git diffs to exclude non-architectural noise (whitespace, lockfiles, minified files)."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    def get_incremental_diff(
        self, base_ref: str = "HEAD~1", target_ref: str = "HEAD"
    ) -> str:
        """Runs git diff with tight context (-U2) excluding noise files."""
        exclude_patterns = [
            ":(exclude)*.lock",
            ":(exclude)*.json",
            ":(exclude)*.min.*",
            ":(exclude)docs/AI_ARCHITECTURE_MAP.md",
            ":(exclude)*.svg",
            ":(exclude)*.png",
        ]

        cmd = [
            "git",
            "diff",
            "-U2",
            "--no-color",
            "--ignore-all-space",
            base_ref,
            target_ref,
            "--",
            ".",
        ] + exclude_patterns

        try:
            res = subprocess.run(
                cmd, cwd=self.repo_path, capture_output=True, text=True, check=True
            )
            raw_diff = res.stdout.strip()
            if not raw_diff:
                return "No semantic structural diff detected."
            return raw_diff
        except subprocess.CalledProcessError as e:
            return f"Git diff execution failed: {e.stderr}"
