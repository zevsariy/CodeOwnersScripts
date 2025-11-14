from __future__ import annotations

from pathlib import Path
from typing import Dict, List


class GroupConfigError(RuntimeError):
    """Deprecated placeholder kept for backwards compatibility."""


def load_group_definitions(config_path: Path) -> Dict[str, List[str]]:
    """Former helper retained for compatibility; external configs are no longer supported."""

    raise GroupConfigError(
        "External group configuration files are no longer supported. "
        "Define group aliases inline within CODEOWNERS using '@@Group: @member' or '@@@Group @member'."
    )
