from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .codeowners import CodeownersEntry, load_codeowners
from .repo import list_tracked_files


@dataclass
class OwnershipReport:
    assignments: Dict[str, Optional[CodeownersEntry]]
    hit_counts: List[int]


def _compute_assignments(entries: Sequence[CodeownersEntry], paths: Iterable[str]) -> OwnershipReport:
    assignments: Dict[str, Optional[CodeownersEntry]] = {}
    hit_counts = [0 for _ in entries]
    for path in paths:
        match_index: Optional[int] = None
        for index, entry in enumerate(entries):
            if entry.matches(path):
                match_index = index
        if match_index is not None:
            assignments[path] = entries[match_index]
            hit_counts[match_index] += 1
        else:
            assignments[path] = None
    return OwnershipReport(assignments=assignments, hit_counts=hit_counts)


def find_unused_entries(entries: Sequence[CodeownersEntry], repo_files: Iterable[str]) -> List[CodeownersEntry]:
    report = _compute_assignments(entries, repo_files)
    return [entry for entry, count in zip(entries, report.hit_counts) if count == 0]


def find_unowned_paths(entries: Sequence[CodeownersEntry], repo_files: Iterable[str]) -> List[str]:
    report = _compute_assignments(entries, repo_files)
    return sorted(path for path, entry in report.assignments.items() if entry is None)


def load_entries_and_repo_files(codeowners_path: Path, repo_root: Path) -> Tuple[List[CodeownersEntry], List[str]]:
    entries = load_codeowners(codeowners_path)
    files = list_tracked_files(repo_root)
    return entries, files
