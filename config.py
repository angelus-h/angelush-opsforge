import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Automatically load .env file if present
env_file = BASE_DIR / ".env"
if env_file.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file)
    except ImportError:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val

# Dedicated state directory hierarchy
STATE_DIR = BASE_DIR / "state"
MAPS_DIR = STATE_DIR / "maps"
LOGS_DIR = STATE_DIR / "logs"
DIGESTS_DIR = STATE_DIR / "digests"
BUNDLES_DIR = STATE_DIR / "bundles"

# Ensure all state directories exist
for directory in (MAPS_DIR, LOGS_DIR, DIGESTS_DIR, BUNDLES_DIR):
    directory.mkdir(parents=True, exist_ok=True)

# Configurable user identification & workspace URLs
SRE_USER_HANDLE = os.getenv("SRE_USER_HANDLE", f"@{os.getenv('USER', 'username')}")
SLACK_WORKSPACE_URL = os.getenv("SLACK_WORKSPACE_URL", "https://slack.com").rstrip("/")
GITLAB_URL = os.getenv("GITLAB_URL", "https://gitlab.com").rstrip("/")
JIRA_URL = os.getenv("JIRA_URL", "https://jira.example.com").rstrip("/")
PAGERDUTY_API_TOKEN = os.getenv("PAGERDUTY_API_TOKEN") or os.getenv(
    "PAGERDUTY_API_KEY", ""
)
PAGERDUTY_API_URL = os.getenv("PAGERDUTY_API_URL", "https://api.pagerduty.com").rstrip(
    "/"
)

# Default base directory for repositories (reads REPOS_DIR or falls back to ~/repos)
DEFAULT_REPOS_DIR = Path(os.getenv("REPOS_DIR", Path.home() / "repos"))


def discover_local_repos(base_dir: Path = DEFAULT_REPOS_DIR) -> list[str]:
    """Auto-discovers git repositories inside the base repos directory."""
    if not base_dir.exists() or not base_dir.is_dir():
        return []

    repos = []
    for item in sorted(base_dir.iterdir()):
        if item.is_dir() and (item / ".git").exists():
            repos.append(str(item))

    if not repos:
        repos = [str(item) for item in sorted(base_dir.iterdir()) if item.is_dir()]

    return repos


DEFAULT_REPOS = discover_local_repos()

# Default investigations output directory
INVESTIGATIONS_DIR = Path(
    os.getenv(
        "INVESTIGATIONS_DIR",
        str(BASE_DIR.parent.parent / "investigations" / "detailed"),
    )
)
DEFAULT_INVESTIGATIONS_DIR = INVESTIGATIONS_DIR

# Gemini API settings
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DEFAULT_MODEL = "gemini-2.5-flash"
