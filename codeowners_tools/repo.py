from __future__ import annotations

from pathlib import Path
import subprocess
from typing import List, Sequence


class GitCommandError(RuntimeError):
    """Raised when a Git command fails."""

    def __init__(self, command: Sequence[str], returncode: int, stderr: str) -> None:
        message = f"Command {' '.join(command)} failed with exit code {returncode}: {stderr.strip()}"
        super().__init__(message)
        self.command = command
        self.returncode = returncode
        self.stderr = stderr


def run_git(repo_root: Path, args: Sequence[str]) -> str:
    command = ["git", "-C", str(repo_root), *args]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GitCommandError(command, result.returncode, result.stderr)
    return result.stdout


def list_tracked_files(repo_root: Path) -> List[str]:
    output = run_git(repo_root, ["ls-files"])
    return [line.strip() for line in output.splitlines() if line.strip()]
