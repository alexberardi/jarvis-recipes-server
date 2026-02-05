#!/usr/bin/env python
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


def get_venv_python(repo_root: Path) -> str:
    """Get the venv python path if it exists, otherwise return 'python3'."""
    venv_python = repo_root / "venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return "python3"


def main():
    # Load .env at repo root
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    migrations_url = os.getenv("MIGRATIONS_DATABASE_URL")
    if migrations_url:
        os.environ["DATABASE_URL"] = migrations_url

    # Use venv python if available
    python_path = get_venv_python(repo_root)
    cmd = [python_path, "-m", "alembic", "upgrade", "head"]
    result = subprocess.run(cmd, cwd=repo_root)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()

