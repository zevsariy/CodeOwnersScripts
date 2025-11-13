"""Utilities for working with CODEOWNERS files."""

from .codeowners import CodeownersEntry, load_codeowners, resolve_owner_for_path
from .analysis import (
    find_unused_entries,
    find_unowned_paths,
)
from .git_activity import (
    build_activity_index,
    suggest_owners_for_paths,
)

__all__ = [
    "CodeownersEntry",
    "load_codeowners",
    "resolve_owner_for_path",
    "find_unused_entries",
    "find_unowned_paths",
    "build_activity_index",
    "suggest_owners_for_paths",
]
