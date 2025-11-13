"""Utilities for working with CODEOWNERS files."""

from .codeowners import (
    CodeownersEntry,
    CodeownersParseResult,
    CheckDirective,
    load_codeowners,
    normalize_group_key,
    parse_codeowners,
    resolve_owner_for_path,
)
from .analysis import (
    find_unused_entries,
    find_unowned_paths,
)
from .audit import (
    AuditResult,
    GuardrailStatus,
    evaluate_guardrails,
    generate_audit,
)
from .git_activity import (
    build_activity_index,
    suggest_owners_for_paths,
)
from .groups import load_group_definitions
from .remote import prepare_repository

__all__ = [
    "CodeownersEntry",
    "CheckDirective",
    "CodeownersParseResult",
    "AuditResult",
    "GuardrailStatus",
    "load_codeowners",
    "parse_codeowners",
    "normalize_group_key",
    "resolve_owner_for_path",
    "find_unused_entries",
    "find_unowned_paths",
    "build_activity_index",
    "suggest_owners_for_paths",
    "evaluate_guardrails",
    "generate_audit",
    "prepare_repository",
    "load_group_definitions",
]
