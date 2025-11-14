from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import fnmatch
import re
import shlex
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union


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
    block_index: Optional[int] = field(default=None, repr=False, compare=False)
    raw_owners: List[str] = field(default_factory=list, repr=False, compare=False)
    inline_comment: Optional[str] = field(default=None, repr=False, compare=False)
    indent: str = field(default="", repr=False, compare=False)
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
    block_index: Optional[int] = None


@dataclass
class GroupDefinition:
    normalized_name: str
    display_token: str
    alias_token: str
    members: List[str]
    delimiter: Optional[str]
    delimiter_attached: bool
    comment: Optional[str]
    line_number: int


@dataclass
class CommentLine:
    text: str
    line_number: int
    indent: str = ""


@dataclass
class BlankLine:
    line_number: int


@dataclass
class StandaloneEntryItem:
    entry_index: int
    line_number: int
    indent: str
    entry: Optional[CodeownersEntry] = field(default=None, repr=False, compare=False)


@dataclass
class CheckLineItem:
    check_index: int
    line_number: int
    indent: str
    inline_comment: Optional[str]
    check: Optional[CheckDirective] = field(default=None, repr=False, compare=False)


@dataclass
class BlockItem:
    kind: str
    line_number: int
    indent: str = ""
    entry_index: Optional[int] = None
    inline_comment: Optional[str] = None
    pattern: Optional[str] = None
    text: Optional[str] = None
    check_index: Optional[int] = None
    entry: Optional[CodeownersEntry] = field(default=None, repr=False, compare=False)
    check: Optional[CheckDirective] = field(default=None, repr=False, compare=False)


@dataclass
class CodeownersBlock:
    index: int
    start_line: int
    end_line: Optional[int]
    items: List[BlockItem]


@dataclass
class _BlockContext:
    index: int
    start_line: int
    items: List[BlockItem] = field(default_factory=list)


LayoutItem = Union[CommentLine, BlankLine, GroupDefinition, StandaloneEntryItem, CheckLineItem, CodeownersBlock]


@dataclass
class CodeownersParseResult:
    entries: List[CodeownersEntry]
    checks: List[CheckDirective]
    groups: Dict[str, List[str]]
    group_definitions: List[GroupDefinition]
    blocks: List[CodeownersBlock]
    layout: List[LayoutItem]


@dataclass
class _RawEntry:
    pattern: str
    owners_tokens: List[str]
    line_number: int
    source: Path
    inline_comment: Optional[str] = None
    block_index: Optional[int] = None
    indent: str = ""


def normalize_group_key(key: str) -> str:
    cleaned = key.strip()
    if cleaned.startswith("@@"):
        cleaned = cleaned[2:]
    elif cleaned.startswith("@"):
        cleaned = cleaned[1:]
    return cleaned


def _split_content_and_comment(line: str) -> Tuple[str, Optional[str]]:
    content_chars: List[str] = []
    in_single = False
    in_double = False
    escape = False
    for index, ch in enumerate(line):
        if escape:
            content_chars.append(ch)
            escape = False
            continue
        if ch == "\\" and (in_single or in_double):
            content_chars.append(ch)
            escape = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            content_chars.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            content_chars.append(ch)
            continue
        if ch == "#" and not in_single and not in_double:
            prefix = "".join(content_chars)
            trimmed_prefix = prefix.rstrip()
            trailing = prefix[len(trimmed_prefix) :]
            comment = trailing + line[index:]
            return trimmed_prefix, comment if comment else None
        content_chars.append(ch)
    prefix = "".join(content_chars).rstrip()
    return prefix, None


def _remove_inline_comment(line: str) -> str:
    content, _ = _split_content_and_comment(line)
    return content.strip()


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


GROUP_TRIPLE_PATTERN = re.compile(r"^(@@@[A-Za-z0-9_\-]+)(?:\s+(.*))?$")
GROUP_COLON_PATTERN = re.compile(r"^(@@[A-Za-z0-9_\-]+):(.*)$")
GROUP_EQUALS_PATTERN = re.compile(r"^(@@[A-Za-z0-9_\-]+)\s*=\s*(.*)$")


CHECK_PATTERN = re.compile(
    r"^Check\s*\(\s*(?P<group>@@[A-Za-z0-9_\-]+)\s*(?P<operator>>=|<=|==|!=|>|<)\s*(?P<threshold>\d+)\s*\)\s*$",
    re.IGNORECASE,
)


def _parse_group_definition_line(
    content: str,
    inline_comment: Optional[str],
    line_number: int,
) -> Optional[GroupDefinition]:
    if not content.startswith("@@"):
        return None

    triple_match = GROUP_TRIPLE_PATTERN.match(content)
    if triple_match:
        display_token = triple_match.group(1)
        members_part = (triple_match.group(2) or "").strip()
        members_tokens = _coalesce_at_tokens(_tokenize_line(members_part)) if members_part else []
        alias_token = "@@" + display_token[3:]
        normalized = normalize_group_key(alias_token)
        return GroupDefinition(
            normalized_name=normalized,
            display_token=display_token,
            alias_token=alias_token,
            members=list(members_tokens),
            delimiter=None,
            delimiter_attached=False,
            comment=inline_comment,
            line_number=line_number,
        )

    colon_match = GROUP_COLON_PATTERN.match(content)
    if colon_match:
        display_token = colon_match.group(1)
        members_part = (colon_match.group(2) or "").strip()
        members_tokens = _coalesce_at_tokens(_tokenize_line(members_part)) if members_part else []
        alias_token = display_token
        normalized = normalize_group_key(alias_token)
        return GroupDefinition(
            normalized_name=normalized,
            display_token=display_token,
            alias_token=alias_token,
            members=list(members_tokens),
            delimiter=":",
            delimiter_attached=True,
            comment=inline_comment,
            line_number=line_number,
        )

    equals_match = GROUP_EQUALS_PATTERN.match(content)
    if equals_match:
        display_token = equals_match.group(1)
        members_part = (equals_match.group(2) or "").strip()
        members_tokens = _coalesce_at_tokens(_tokenize_line(members_part)) if members_part else []
        alias_token = display_token
        normalized = normalize_group_key(alias_token)
        return GroupDefinition(
            normalized_name=normalized,
            display_token=display_token,
            alias_token=alias_token,
            members=list(members_tokens),
            delimiter="=",
            delimiter_attached=False,
            comment=inline_comment,
            line_number=line_number,
        )

    return None


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


def parse_codeowners(
    codeowners_path: Path,
) -> CodeownersParseResult:
    raw_entries: List[_RawEntry] = []
    inline_groups: Dict[str, List[str]] = {}
    checks: List[CheckDirective] = []
    group_definitions: List[GroupDefinition] = []
    blocks: List[CodeownersBlock] = []
    layout: List[LayoutItem] = []
    current_block: Optional[_BlockContext] = None

    with codeowners_path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            raw_line = line.rstrip("\n")
            content, inline_comment = _split_content_and_comment(raw_line)
            stripped = content.strip()
            indent = raw_line[: len(raw_line) - len(raw_line.lstrip())]

            if current_block is not None:
                if stripped == "}":
                    block = CodeownersBlock(
                        index=current_block.index,
                        start_line=current_block.start_line,
                        end_line=index,
                        items=current_block.items,
                    )
                    blocks.append(block)
                    layout.append(block)
                    current_block = None
                    continue

                if not stripped and not (inline_comment and inline_comment.strip()):
                    current_block.items.append(BlockItem(kind="blank", line_number=index))
                    continue

                if stripped.startswith("#"):
                    current_block.items.append(
                        BlockItem(
                            kind="comment",
                            line_number=index,
                            indent=indent,
                            text=raw_line.strip(),
                        )
                    )
                    continue

                if stripped.lower().startswith("check"):
                    check = _parse_check(raw_line, index, codeowners_path)
                    if check:
                        check.block_index = current_block.index
                        checks.append(check)
                        check_index = len(checks) - 1
                        current_block.items.append(
                            BlockItem(
                                kind="check",
                                line_number=index,
                                indent=indent,
                                check_index=check_index,
                                inline_comment=inline_comment,
                            )
                        )
                    continue

                tokens = _coalesce_at_tokens(_tokenize_line(content))
                if len(tokens) >= 2:
                    pattern = tokens[0]
                    owners_tokens = tokens[1:]
                    raw_entries.append(
                        _RawEntry(
                            pattern=pattern,
                            owners_tokens=owners_tokens,
                            line_number=index,
                            source=codeowners_path,
                            inline_comment=inline_comment,
                            block_index=current_block.index,
                            indent=indent,
                        )
                    )
                    entry_index = len(raw_entries) - 1
                    current_block.items.append(
                        BlockItem(
                            kind="entry",
                            line_number=index,
                            indent=indent,
                            entry_index=entry_index,
                        )
                    )
                    continue

                if len(tokens) == 1:
                    current_block.items.append(
                        BlockItem(
                            kind="pattern",
                            line_number=index,
                            indent=indent,
                            pattern=tokens[0],
                            inline_comment=inline_comment,
                        )
                    )
                    continue

                if stripped:
                    current_block.items.append(
                        BlockItem(
                            kind="raw",
                            line_number=index,
                            indent=indent,
                            text=stripped,
                        )
                    )
                continue

            if not stripped:
                layout.append(BlankLine(line_number=index))
                continue

            if stripped.startswith("#"):
                layout.append(CommentLine(text=raw_line.strip(), line_number=index, indent=indent))
                continue

            if stripped == "{":
                current_block = _BlockContext(index=len(blocks), start_line=index)
                continue

            if stripped == "}":
                # unmatched closing brace, ignore gracefully
                continue

            if stripped.lower().startswith("check"):
                check = _parse_check(raw_line, index, codeowners_path)
                if check:
                    checks.append(check)
                    check_index = len(checks) - 1
                    layout.append(
                        CheckLineItem(
                            check_index=check_index,
                            line_number=index,
                            indent=indent,
                            inline_comment=inline_comment,
                        )
                    )
                continue

            group_definition = _parse_group_definition_line(stripped, inline_comment, index)
            if group_definition:
                inline_groups[group_definition.alias_token] = list(group_definition.members)
                group_definitions.append(group_definition)
                layout.append(group_definition)
                continue

            tokens = _coalesce_at_tokens(_tokenize_line(content))
            if len(tokens) >= 2:
                pattern = tokens[0]
                owners_tokens = tokens[1:]
                raw_entries.append(
                    _RawEntry(
                        pattern=pattern,
                        owners_tokens=owners_tokens,
                        line_number=index,
                        source=codeowners_path,
                        inline_comment=inline_comment,
                        indent=indent,
                    )
                )
                entry_index = len(raw_entries) - 1
                layout.append(
                    StandaloneEntryItem(
                        entry_index=entry_index,
                        line_number=index,
                        indent=indent,
                    )
                )
                continue

            # Preserve any non-empty raw lines that do not match known constructs as comments
            if raw_line.strip():
                layout.append(CommentLine(text=raw_line.strip(), line_number=index, indent=indent))

    if current_block is not None:
        block = CodeownersBlock(
            index=current_block.index,
            start_line=current_block.start_line,
            end_line=None,
            items=current_block.items,
        )
        blocks.append(block)
        layout.append(block)

    merged_groups = {normalize_group_key(key): list(values) for key, values in inline_groups.items()}

    resolved_groups: Dict[str, List[str]] = {}
    for key in merged_groups:
        normalized = normalize_group_key(key)
        try:
            resolved_groups[normalized] = _resolve_group(key, merged_groups)
        except ValueError:
            resolved_groups[normalized] = list(merged_groups[key])

    entries: List[CodeownersEntry] = []
    entry_lookup: Dict[int, CodeownersEntry] = {}
    for idx, raw_entry in enumerate(raw_entries):
        owners = _expand_owner_tokens(raw_entry.owners_tokens, merged_groups)
        entry = CodeownersEntry(
            pattern=raw_entry.pattern,
            owners=owners,
            line_number=raw_entry.line_number,
            source=raw_entry.source,
        )
        entry.block_index = raw_entry.block_index
        entry.raw_owners = list(raw_entry.owners_tokens)
        entry.inline_comment = raw_entry.inline_comment
        entry.indent = raw_entry.indent
        entries.append(entry)
        entry_lookup[idx] = entry

    for item in layout:
        if isinstance(item, StandaloneEntryItem) and item.entry_index in entry_lookup:
            item.entry = entry_lookup[item.entry_index]
        elif isinstance(item, CodeownersBlock):
            for block_item in item.items:
                if block_item.entry_index is not None and block_item.entry_index in entry_lookup:
                    block_item.entry = entry_lookup[block_item.entry_index]

    check_lookup: Dict[int, CheckDirective] = {idx: check for idx, check in enumerate(checks)}
    for item in layout:
        if isinstance(item, CheckLineItem) and item.check_index in check_lookup:
            item.check = check_lookup[item.check_index]
        elif isinstance(item, CodeownersBlock):
            for block_item in item.items:
                if block_item.check_index is not None and block_item.check_index in check_lookup:
                    block_item.check = check_lookup[block_item.check_index]

    return CodeownersParseResult(
        entries=entries,
        checks=checks,
        groups=resolved_groups,
        group_definitions=group_definitions,
        blocks=blocks,
        layout=layout,
    )


def load_codeowners(codeowners_path: Path) -> List[CodeownersEntry]:
    return parse_codeowners(codeowners_path).entries


def resolve_owner_for_path(entries: Iterable[CodeownersEntry], path: str) -> Optional[CodeownersEntry]:
    match: Optional[CodeownersEntry] = None
    for entry in entries:
        if entry.matches(path):
            match = entry
    return match
