from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from .codeowners import normalize_group_key


class GroupConfigError(RuntimeError):
    """Raised when a group configuration file cannot be parsed."""


def _load_from_mapping(mapping: Dict[str, Iterable[str]]) -> Dict[str, List[str]]:
    expanded: Dict[str, List[str]] = {}
    for raw_key, values in mapping.items():
        normalized = normalize_group_key(raw_key)
        if isinstance(values, str):
            tokens = values.split()
        else:
            tokens = [str(value).strip() for value in values if str(value).strip()]
        expanded[normalized] = tokens
    return expanded


def _load_plain_text(path: Path) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if ":" in stripped:
                key, _, raw_values = stripped.partition(":")
            elif "=" in stripped:
                key, _, raw_values = stripped.partition("=")
            else:
                raise GroupConfigError(
                    f"Invalid group definition at {path}:{line_number}: expected ':' or '=' delimiter"
                )
            tokens = raw_values.strip().split()
            mapping[normalize_group_key(key)] = tokens
    return mapping


def load_group_definitions(config_path: Path) -> Dict[str, List[str]]:
    """Load group definitions from json, yaml (if available), or plain text format."""
    if not config_path.exists():
        raise GroupConfigError(f"Group config file not found: {config_path}")

    suffix = config_path.suffix.lower()
    if suffix == ".json":
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise GroupConfigError("JSON group config must be an object mapping group to members")
        return _load_from_mapping(data)

    if suffix in {".yml", ".yaml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise GroupConfigError("PyYAML is required to read YAML group configs") from exc
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise GroupConfigError("YAML group config must be a mapping of group to members")
        return _load_from_mapping(data)

    return _load_plain_text(config_path)
