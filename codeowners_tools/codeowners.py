from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import fnmatch
import re
import shlex
from typing import Iterable, List, Optional


def _normalize_path(path: str) -> str:
    """Convert a filesystem path to a CODEOWNERS-style POSIX relative path."""
    return PurePosixPath(path).as_posix().lstrip("/")


def _ensure_directory_glob(pattern: str, is_directory: bool) -> str:
    """Ensure directory patterns match the entire subtree."""
    if not is_directory:
        return pattern or "**"
    cleaned = pattern.rstrip("/")
    if not cleaned:
        return "**"
    if cleaned.endswith("/**"):
        return cleaned
    return f"{cleaned}/**"


def _expand_pattern_variants(pattern: str) -> List[str]:
    """Expand a CODEOWNERS pattern into glob variants we can evaluate."""
    raw = pattern.strip()
    anchored = raw.startswith("/")
    is_directory = raw.endswith("/")
    body = raw.strip("/")
    body = _ensure_directory_glob(body, is_directory)

    variants: List[str] = []
    if anchored:
        variants.append(body)
    else:
        variants.append(body)
        prefixed = body if body.startswith("**") else _ensure_directory_glob(f"**/{body}", is_directory)
        variants.append(prefixed)

    # Remove duplicates while preserving order
    seen = set()
    ordered_variants: List[str] = []
    for variant in variants:
        key = variant.lstrip("/") or "**"
        if key not in seen:
            seen.add(key)
            ordered_variants.append(key)
    return ordered_variants


def _compile_glob(pattern: str) -> re.Pattern[str]:
    regex = fnmatch.translate(pattern)
    return re.compile(regex)


@dataclass
class CodeownersEntry:
    pattern: str
    owners: List[str]
    line_number: int
    source: Path
    _compiled_variants: List[re.Pattern[str]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        variants = _expand_pattern_variants(self.pattern)
        self._compiled_variants = [_compile_glob(variant) for variant in variants]

    def matches(self, path: str) -> bool:
        posix_path = _normalize_path(path)
        for compiled in self._compiled_variants:
            if compiled.fullmatch(posix_path):
                return True
        return False


def load_codeowners(codeowners_path: Path) -> List[CodeownersEntry]:
    entries: List[CodeownersEntry] = []
    with codeowners_path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                parts = shlex.split(line, comments=False)
            except ValueError:
                # Fallback to plain split if shlex cannot parse
                parts = line.split()
            if len(parts) < 2:
                continue
            pattern, *owners = parts
            entry = CodeownersEntry(pattern=pattern, owners=owners, line_number=index, source=codeowners_path)
            entries.append(entry)
    return entries


def resolve_owner_for_path(entries: Iterable[CodeownersEntry], path: str) -> Optional[CodeownersEntry]:
    match: Optional[CodeownersEntry] = None
    for entry in entries:
        if entry.matches(path):
            match = entry
    return match
