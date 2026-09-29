import subprocess
import shutil
import json
from typing import Optional, List

class LLMBridge:
    """Invokes local 'llm' CLI or Ollama models for stateless, zero-waste execution."""

    MODEL_FLASH = "gemini-3.6-flash"
    MODEL_FLASH_LITE = "gemini-flash-lite-latest"

    @classmethod
    def is_available(cls) -> bool:
        return shutil.which("llm") is not None

    @classmethod
    def is_ollama_available(cls) -> bool:
        return shutil.which("ollama") is not None

    @classmethod
    def list_available_models(cls) -> List[str]:
        """Discovers available models dynamically from `llm models` and local Ollama instances."""
        models = [cls.MODEL_FLASH, cls.MODEL_FLASH_LITE]

        # 1. Try discovering models from `llm models` CLI
        if cls.is_available():
            try:
                res = subprocess.run(["llm", "models", "list"], capture_output=True, text=True, timeout=5)
                for line in res.stdout.splitlines():
                    clean = line.strip()
                    if clean and not clean.startswith("Default:") and not clean.startswith("Extra:"):
                        model_name = clean.split()[0].strip(":")
                        if model_name and model_name not in models:
                            models.append(model_name)
            except Exception:
                pass

        # 2. Try discovering models directly from Ollama if running
        if cls.is_ollama_available():
            try:
                res = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
                lines = res.stdout.splitlines()
                if len(lines) > 1:
                    for line in lines[1:]:
                        parts = line.split()
                        if parts:
                            ollama_model = f"ollama/{parts[0]}"
                            if ollama_model not in models:
                                models.append(ollama_model)
            except Exception:
                pass

        # Add sensible local defaults for prospective MacBook Pro / Ollama users
        candidate_defaults = [
            "qwen2.5-coder:14b",
            "qwen2.5-coder:32b",
            "qwen2.5:7b",
            "deepseek-r1:32b"
        ]
        for cand in candidate_defaults:
            if cand not in models:
                models.append(cand)

        return models

    @classmethod
    def execute(cls, prompt: str, model: str = MODEL_FLASH, system_prompt: Optional[str] = None) -> str:
        """Executes prompt via `llm` CLI or directly through `ollama run`."""
        # Check if direct Ollama execution is selected
        if model.startswith("ollama/") or ":" in model and not model.startswith("gemini"):
            clean_model = model.replace("ollama/", "")
            if cls.is_ollama_available():
                cmd = ["ollama", "run", clean_model]
                full_prompt = f"System: {system_prompt}\n\n{prompt}" if system_prompt else prompt
                try:
                    res = subprocess.run(cmd, input=full_prompt, capture_output=True, text=True, check=True)
                    return res.stdout.strip()
                except subprocess.CalledProcessError as e:
                    return f"Ollama execution failed (exit code {e.returncode}):\n{e.stderr or e.stdout}"

        # Standard path: via `llm` CLI
        if not cls.is_available():
            raise RuntimeError("The 'llm' CLI binary is not installed or not in PATH.")

        models_to_try = [model]
        if "gemini" in model.lower():
            for fallback in ["gemini-3.6-flash", "gemini-flash-lite-latest"]:
                if fallback not in models_to_try:
                    models_to_try.append(fallback)

        last_error = ""
        for m in models_to_try:
            cmd = ["llm", "-m", m]
            if system_prompt:
                cmd.extend(["-s", system_prompt])

            try:
                res = subprocess.run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    check=True
                )
                return res.stdout.strip()
            except subprocess.CalledProcessError as e:
                last_error = e.stderr or e.stdout
                continue

        return f"LLM execution failed: {last_error.strip()}"
