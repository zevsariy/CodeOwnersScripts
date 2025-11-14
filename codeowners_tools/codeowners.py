from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import fnmatch
import re
import shlex
from typing import Dict, Iterable, List, Optional, Sequence, Set


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


@dataclass
class CheckDirective:
    group: str
    operator: str
    threshold: int
    raw: str
    line_number: int
    source: Path


@dataclass
class CodeownersParseResult:
    entries: List[CodeownersEntry]
    checks: List[CheckDirective]
    groups: Dict[str, List[str]]


@dataclass
class _RawEntry:
    pattern: str
    owners: List[str]
    line_number: int
    source: Path


def normalize_group_key(key: str) -> str:
    cleaned = key.strip()
    if cleaned.startswith("@@"):
        cleaned = cleaned[2:]
    elif cleaned.startswith("@"):
        cleaned = cleaned[1:]
    return cleaned


def _remove_inline_comment(line: str) -> str:
    result: List[str] = []
    in_single = False
    in_double = False
    escape = False
    for ch in line:
        if escape:
            result.append(ch)
            escape = False
            continue
        if ch == "\\" and (in_single or in_double):
            result.append(ch)
            escape = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            result.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            result.append(ch)
            continue
        if ch == "#" and not in_single and not in_double:
            break
        result.append(ch)
    return "".join(result).strip()


def _tokenize_line(line: str) -> List[str]:
    cleaned = _remove_inline_comment(line)
    if not cleaned:
        return []
    try:
        return shlex.split(cleaned, comments=False)
    except ValueError:
        return cleaned.split()


def _coalesce_at_tokens(tokens: Sequence[str]) -> List[str]:
    """Merge stray '@' prefixes that were separated by whitespace."""
    normalized: List[str] = []
    pending: Optional[str] = None
    for token in tokens:
        if pending is not None:
            normalized.append(pending + token)
            pending = None
            continue
        if token in {"@", "@@"}:
            pending = token
            continue
        normalized.append(token)
    if pending is not None:
        normalized.append(pending)
    return normalized


CHECK_PATTERN = re.compile(
    r"^Check\s*\(\s*(?P<group>@@[A-Za-z0-9_\-]+)\s*(?P<operator>>=|<=|==|!=|>|<)\s*(?P<threshold>\d+)\s*\)\s*$",
    re.IGNORECASE,
)


def _parse_check(line: str, line_number: int, source: Path) -> Optional[CheckDirective]:
    cleaned = _remove_inline_comment(line)
    if not cleaned.lower().startswith("check"):
        return None
    match = CHECK_PATTERN.match(cleaned)
    if not match:
        return CheckDirective(
            group="",
            operator="",
            threshold=0,
            raw=cleaned,
            line_number=line_number,
            source=source,
        )
    raw_group = match.group("group")
    operator = match.group("operator")
    threshold = int(match.group("threshold"))
    return CheckDirective(
        group=normalize_group_key(raw_group),
        operator=operator,
        threshold=threshold,
        raw=cleaned,
        line_number=line_number,
        source=source,
    )


def _resolve_group(name: str, definitions: Dict[str, Sequence[str]], seen: Optional[Set[str]] = None) -> List[str]:
    normalized = normalize_group_key(name)
    if seen is None:
        seen = set()
    if normalized in seen:
        raise ValueError(f"Detected recursive definition for @@{normalized}")
    seen.add(normalized)
    tokens = list(definitions.get(normalized, []))
    expanded: List[str] = []
    for token in tokens:
        if token.startswith("@@"):
            expanded.extend(_resolve_group(token, definitions, seen))
        else:
            expanded.append(token)
    seen.remove(normalized)
    return expanded


def _expand_owner_tokens(tokens: Sequence[str], definitions: Dict[str, Sequence[str]]) -> List[str]:
    expanded: List[str] = []
    for token in tokens:
        if token.startswith("@@"):
            group_name = normalize_group_key(token)
            if group_name in definitions:
                expanded.extend(_resolve_group(group_name, definitions))
            else:
                expanded.append(token)
        else:
            expanded.append(token)
    return expanded


def _merge_group_definitions(
    provided: Optional[Dict[str, Sequence[str]]],
    inline: Dict[str, List[str]],
) -> Dict[str, List[str]]:
    merged: Dict[str, List[str]] = {}
    if provided:
        for key, values in provided.items():
            normalized = normalize_group_key(key)
            merged[normalized] = list(values)
    for key, values in inline.items():
        merged[normalize_group_key(key)] = list(values)
    return merged


def parse_codeowners(
    codeowners_path: Path,
    *,
    group_definitions: Optional[Dict[str, Sequence[str]]] = None,
) -> CodeownersParseResult:
    raw_entries: List[_RawEntry] = []
    inline_groups: Dict[str, List[str]] = {}
    checks: List[CheckDirective] = []

    with codeowners_path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            if stripped in {"{", "}"}:
                continue

            if stripped.lower().startswith("check"):
                check = _parse_check(line, index, codeowners_path)
                if check:
                    checks.append(check)
                continue

            if stripped.startswith("@@") and (":" in stripped or "=" in stripped) and stripped.split()[0].startswith("@@"):
                delimiter = ":" if ":" in stripped else "="
                name, _, remainder = stripped.partition(delimiter)
                owners_tokens = _coalesce_at_tokens(_tokenize_line(remainder))
                inline_groups[name.strip()] = owners_tokens
                continue

            if stripped.startswith("@@@"):
                tokens = _tokenize_line(line)
                if len(tokens) >= 2:
                    group_token = tokens[0]
                    owners_tokens = _coalesce_at_tokens(tokens[1:])
                    alias = "@@" + group_token[3:]
                    inline_groups[alias] = owners_tokens
                continue

            parts = _tokenize_line(line)
            if len(parts) < 2:
                continue
            pattern, owners_tokens = parts[0], _coalesce_at_tokens(parts[1:])
            raw_entries.append(
                _RawEntry(
                    pattern=pattern,
                    owners=owners_tokens,
                    line_number=index,
                    source=codeowners_path,
                )
            )

    merged_groups = _merge_group_definitions(group_definitions, inline_groups)

    resolved_groups: Dict[str, List[str]] = {}
    for key in merged_groups:
        normalized = normalize_group_key(key)
        try:
            resolved_groups[normalized] = _resolve_group(key, merged_groups)
        except ValueError:
            resolved_groups[normalized] = list(merged_groups[key])

    entries: List[CodeownersEntry] = []
    for raw_entry in raw_entries:
        owners = _expand_owner_tokens(raw_entry.owners, merged_groups)
        entry = CodeownersEntry(
            pattern=raw_entry.pattern,
            owners=owners,
            line_number=raw_entry.line_number,
            source=raw_entry.source,
        )
        entries.append(entry)

    return CodeownersParseResult(entries=entries, checks=checks, groups=resolved_groups)


def load_codeowners(
    codeowners_path: Path,
    *,
    group_definitions: Optional[Dict[str, Sequence[str]]] = None,
) -> List[CodeownersEntry]:
    return parse_codeowners(codeowners_path, group_definitions=group_definitions).entries


def resolve_owner_for_path(entries: Iterable[CodeownersEntry], path: str) -> Optional[CodeownersEntry]:
    match: Optional[CodeownersEntry] = None
    for entry in entries:
        if entry.matches(path):
            match = entry
    return match
