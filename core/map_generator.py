import ast
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Any
import yaml


class RepoScanner:
    """Performs 100% local, zero-token structural analysis on target repositories."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()
        if not self.repo_path.exists():
            raise FileNotFoundError(f"Repository not found at {repo_path}")

    def get_git_commit(self) -> str:
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return res.stdout.strip()
        except Exception:
            return "unknown"

    def get_file_tree(self, max_depth: int = 3) -> str:
        """Returns filtered file tree excluding hidden dirs, caches, virtualenvs."""
        ignore_dirs = {
            ".git",
            ".venv",
            "venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".idea",
            ".vscode",
            "node_modules",
            ".tox",
        }
        lines: List[str] = []
        len(self.repo_path.parts)

        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = [
                d for d in dirs if d not in ignore_dirs and not d.startswith(".")
            ]
            rel_path = Path(root).relative_to(self.repo_path)
            depth = len(rel_path.parts)
            if depth > max_depth:
                continue

            indent = "  " * depth
            if depth > 0:
                lines.append(f"{indent[:-2]}📁 {rel_path.name}/")
            for f in sorted(files):
                if not f.startswith(".") and not f.endswith((".pyc", ".retry")):
                    lines.append(f"{indent}📄 {f}")

        return "\n".join(lines[:300])  # Cap at 300 lines for structural clarity

    def extract_python_ast(self, max_files: int = 20) -> Dict[str, List[str]]:
        """Extracts top-level class and function signatures without function bodies."""
        signatures: Dict[str, List[str]] = {}
        py_files = sorted(list(self.repo_path.rglob("*.py")))

        count = 0
        for py_file in py_files:
            rel = str(py_file.relative_to(self.repo_path))
            if any(
                part.startswith((".", "venv", "__pycache__")) for part in py_file.parts
            ):
                continue

            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
                file_sigs = []
                for node in ast.iter_child_nodes(tree):
                    if isinstance(node, ast.FunctionDef):
                        args = [arg.arg for arg in node.args.args]
                        file_sigs.append(f"def {node.name}({', '.join(args)})")
                    elif isinstance(node, ast.ClassDef):
                        bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
                        base_str = f"({', '.join(bases)})" if bases else ""
                        file_sigs.append(f"class {node.name}{base_str}")
                if file_sigs:
                    signatures[rel] = file_sigs
                    count += 1
                    if count >= max_files:
                        break
            except Exception:
                continue

        return signatures

    def extract_ansible_surface(self) -> Dict[str, Any]:
        """Extracts key variables and play entrypoints from Ansible repositories."""
        ansible_meta: Dict[str, Any] = {
            "entry_playbooks": [],
            "roles": [],
            "group_vars_keys": {},
        }

        # Check entry playbooks
        for yml in self.repo_path.glob("*.yml"):
            if yml.name.startswith("."):
                continue
            ansible_meta["entry_playbooks"].append(yml.name)

        # Check roles
        roles_dir = self.repo_path / "roles"
        if roles_dir.exists() and roles_dir.is_dir():
            ansible_meta["roles"] = [
                d.name
                for d in roles_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            ]

        # Extract variable keys from group_vars
        gv_dir = self.repo_path / "group_vars"
        if gv_dir.exists() and gv_dir.is_dir():
            for gv_file in gv_dir.rglob("*.yml"):
                try:
                    content = yaml.safe_load(gv_file.read_text(encoding="utf-8"))
                    if isinstance(content, dict):
                        rel = str(gv_file.relative_to(self.repo_path))
                        ansible_meta["group_vars_keys"][rel] = list(content.keys())
                except Exception:
                    continue

        return ansible_meta

    def build_cold_payload(self) -> str:
        """Assembles a dense structural baseline representation (0 tokens used)."""
        commit = self.get_git_commit()
        tree = self.get_file_tree()
        py_sigs = self.extract_python_ast()
        ansible_meta = self.extract_ansible_surface()

        payload = [
            f"# REPOSITORY SCAN: {self.repo_path.name}",
            f"Git Commit: {commit}",
            f"Path: {self.repo_path}\n",
            "## 1. FILE TREE (Trimmed)",
            "```",
            tree,
            "```\n",
        ]

        if ansible_meta["entry_playbooks"] or ansible_meta["roles"]:
            payload.append("## 2. ANSIBLE SURFACE")
            payload.append(
                f"- Entry Playbooks: {', '.join(ansible_meta['entry_playbooks'])}"
            )
            payload.append(f"- Roles: {', '.join(ansible_meta['roles'])}")
            if ansible_meta["group_vars_keys"]:
                payload.append("- Group Vars Keys:")
                for k, vars_list in ansible_meta["group_vars_keys"].items():
                    payload.append(f"  - `{k}`: {', '.join(vars_list[:15])}")
            payload.append("")

        if py_sigs:
            payload.append("## 3. PYTHON AST SKELETON")
            for fpath, sigs in py_sigs.items():
                payload.append(f"- `{fpath}`:")
                for s in sigs:
                    payload.append(f"    {s}")
            payload.append("")

        return "\n".join(payload)
