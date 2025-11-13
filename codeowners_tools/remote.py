from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import subprocess
import tempfile
from typing import Optional


class GitCloneError(RuntimeError):
    """Raised when cloning a repository fails."""

    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        message = f"Command {' '.join(command)} failed with exit code {returncode}: {stderr.strip()}"
        super().__init__(message)
        self.command = command
        self.returncode = returncode
        self.stderr = stderr


def _clone_repository(repo_url: str, branch: Optional[str], stack: ExitStack) -> Path:
    temp_dir = stack.enter_context(tempfile.TemporaryDirectory(prefix="codeowners-audit-"))
    destination = Path(temp_dir) / "repo"

    command = ["git", "clone"]
    if branch:
        command.extend(["--branch", branch, "--single-branch"])
    command.extend([repo_url, str(destination)])

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GitCloneError(command, result.returncode, result.stderr)
    return destination


def prepare_repository(
    stack: ExitStack,
    repo_root: Optional[Path],
    repo_url: Optional[str],
    branch: Optional[str],
) -> Path:
    """Return a usable repository path, cloning if a URL is provided."""
    if repo_url:
        return _clone_repository(repo_url, branch, stack)

    if repo_root is None:
        return Path.cwd()

    return repo_root.resolve()
