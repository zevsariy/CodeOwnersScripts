from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Sequence

from .repo import run_git


def _normalize(path: str) -> str:
    return PurePosixPath(path).as_posix().strip("/")


def _normalize_target(path: str) -> str:
    normalized = PurePosixPath(path).as_posix().strip("/")
    return normalized


@dataclass
class GroupSuggestion:
    group_name: str
    matching_members: List[str]
    member_count: int


@dataclass
class CandidateSuggestion:
    identity: str
    commits: int
    share: float
    groups: List[str] = None


@dataclass
class PathOwnershipSuggestion:
    path: str
    is_directory: bool
    total_commits: int
    candidates: List[CandidateSuggestion]
    group_suggestions: List[GroupSuggestion] = None


@dataclass
class GitActivityIndex:
    file_commits: Dict[str, Counter]
    directory_commits: Dict[str, Counter]

    def counts_for_path(self, target: str, treat_as_directory: bool = False) -> Counter:
        key = _normalize_target(target)
        if not key and treat_as_directory:
            return self.directory_commits.get("", Counter())
        if not treat_as_directory and key in self.file_commits:
            return self.file_commits[key]
        return self.directory_commits.get(key, Counter())


def build_activity_index(
    repo_root: Path,
    include_merges: bool = False,
    since: Optional[str] = None,
    paths: Optional[Sequence[str]] = None,
) -> GitActivityIndex:
    args: List[str] = ["log", "--name-only", "--pretty=format:%an\t%ae"]
    if not include_merges:
        args.append("--no-merges")
    if since:
        args.append(f"--since={since}")
    if paths:
        posix_paths = [PurePosixPath(path).as_posix() for path in paths]
        args.append("--")
        args.extend(posix_paths)

    output = run_git(repo_root, args)

    file_commits: Dict[str, Counter] = defaultdict(Counter)
    directory_commits: Dict[str, Counter] = defaultdict(Counter)

    current_identity: Optional[str] = None
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if "\t" in line:
            name, email = line.split("\t", 1)
            identity = f"{name.strip()} <{email.strip()}>"
            current_identity = identity
            continue
        if current_identity is None:
            continue
        path = line.strip()
        if not path:
            continue
        if " -> " in path:
            path = path.split(" -> ", 1)[-1]
        normalized = _normalize(path)
        if not normalized:
            continue
        file_commits[normalized][current_identity] += 1
        parts = normalized.split("/")
        directory_commits[""].update({current_identity: 1})
        for depth in range(1, len(parts)):
            prefix = "/".join(parts[:depth])
            directory_commits[prefix][current_identity] += 1
    return GitActivityIndex(file_commits=dict(file_commits), directory_commits=dict(directory_commits))


def _find_groups_for_identity(identity: str, groups: Dict[str, List[str]]) -> List[str]:
    """Find all groups that contain the given identity."""
    if not groups:
        return []
    matching_groups: List[str] = []
    for group_name, members in groups.items():
        if identity in members:
            matching_groups.append(group_name)
    return matching_groups


def _build_suggestions(
    counter: Counter,
    limit: int,
    min_commits: int,
    groups: Optional[Dict[str, List[str]]] = None,
) -> List[CandidateSuggestion]:
    total = sum(counter.values())
    if total == 0:
        return []
    suggestions: List[CandidateSuggestion] = []
    for identity, commits in counter.most_common():
        if commits < min_commits:
            continue
        share = commits / total if total else 0.0
        identity_groups = _find_groups_for_identity(identity, groups) if groups else []
        suggestions.append(
            CandidateSuggestion(
                identity=identity,
                commits=commits,
                share=share,
                groups=identity_groups if identity_groups else None,
            )
        )
        if len(suggestions) >= limit:
            break
    return suggestions


def _aggregate_group_suggestions(
    candidates: List[CandidateSuggestion],
    groups: Dict[str, List[str]],
    top_n: int = 5,
) -> List[GroupSuggestion]:
    """Aggregate group suggestions from top committers, showing groups with most matching members."""
    if not candidates or not groups:
        return []
    
    # Collect all groups mentioned by candidates
    group_members: Dict[str, set[str]] = {}
    for candidate in candidates:
        if candidate.groups:
            for group_name in candidate.groups:
                if group_name not in group_members:
                    group_members[group_name] = set()
                group_members[group_name].add(candidate.identity)
    
    # Build group suggestions sorted by number of matching members
    group_suggestions: List[GroupSuggestion] = []
    for group_name, matching in group_members.items():
        group_suggestions.append(
            GroupSuggestion(
                group_name=group_name,
                matching_members=sorted(matching),
                member_count=len(matching),
            )
        )
    
    # Sort by member count descending, then alphabetically
    group_suggestions.sort(key=lambda g: (-g.member_count, g.group_name))
    
    return group_suggestions[:top_n] if top_n > 0 else group_suggestions


def suggest_owners_for_paths(
    index: GitActivityIndex,
    targets: Iterable[str],
    limit: int = 3,
    min_commits: int = 1,
    groups: Optional[Dict[str, List[str]]] = None,
    suggest_groups: bool = False,
    group_limit: int = 5,
) -> List[PathOwnershipSuggestion]:
    results: List[PathOwnershipSuggestion] = []
    for raw_target in targets:
        treat_as_directory = raw_target.endswith("/")
        counts = index.counts_for_path(raw_target, treat_as_directory=treat_as_directory)
        candidates = _build_suggestions(counts, limit=limit, min_commits=min_commits, groups=groups if suggest_groups else None)
        
        group_suggestions = None
        if suggest_groups and groups and candidates:
            group_suggestions = _aggregate_group_suggestions(candidates, groups, group_limit)
        
        results.append(
            PathOwnershipSuggestion(
                path=raw_target,
                is_directory=treat_as_directory or raw_target.strip("/") not in index.file_commits,
                total_commits=sum(counts.values()),
                candidates=candidates,
                group_suggestions=group_suggestions,
            )
        )
    return results
