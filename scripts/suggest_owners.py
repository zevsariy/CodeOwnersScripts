from __future__ import annotations

import argparse
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codeowners_tools.analysis import find_unowned_paths, load_entries_and_repo_files
from codeowners_tools.git_activity import build_activity_index, suggest_owners_for_paths
from codeowners_tools.groups import GroupConfigError, load_group_definitions
from codeowners_tools.remote import prepare_repository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Suggest potential CODEOWNERS based on Git commit activity.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="Path to the repository root (defaults to current directory when --repo-url is omitted).",
    )
    parser.add_argument(
        "--repo-url",
        help="Git URL to clone for analysis.",
    )
    parser.add_argument(
        "--branch",
        help="Branch or ref to check out (requires --repo-url).",
    )
    parser.add_argument(
        "--codeowners",
        default="CODEOWNERS",
        type=Path,
        help="Path to the CODEOWNERS file relative to the repository root.",
    )
    parser.add_argument(
        "--group-config",
        type=Path,
        help="Optional path to group definitions (relative to the repo when not absolute).",
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        help="Explicit file or directory paths to analyze. Directories can end with '/'.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Maximum number of suggestions per path (default: 3).",
    )
    parser.add_argument(
        "--min-commits",
        type=int,
        default=1,
        help="Minimum number of commits required for a contributor to be suggested.",
    )
    parser.add_argument(
        "--since",
        help="Optional Git --since range (for example '1 year ago').",
    )
    parser.add_argument(
        "--include-merges",
        action="store_true",
        help="Include merge commits when computing suggestions.",
    )
    parser.add_argument(
        "--restrict-to-targets",
        action="store_true",
        help="Limit Git history analysis to the provided target paths for faster execution.",
    )
    return parser.parse_args()


def determine_targets(
    entries: Sequence,
    tracked_files: List[str],
    explicit_paths: Optional[List[str]],
) -> List[str]:
    if explicit_paths:
        return explicit_paths

    unowned = find_unowned_paths(entries, tracked_files)
    if not unowned:
        print("No unowned tracked paths were found; provide --paths to analyze specific targets.")
        return []
    return unowned


def main() -> int:
    args = parse_args()
    if args.repo_root and args.repo_url:
        print("--repo-root cannot be combined with --repo-url", file=sys.stderr)
        return 2
    if args.branch and not args.repo_url:
        print("--branch requires --repo-url", file=sys.stderr)
        return 2

    with ExitStack() as stack:
        repo_root = prepare_repository(stack, args.repo_root, args.repo_url, args.branch)
        codeowners_path = args.codeowners
        if not codeowners_path.is_absolute():
            codeowners_path = repo_root / codeowners_path

        if not codeowners_path.exists():
            print(f"CODEOWNERS file not found: {codeowners_path}", file=sys.stderr)
            return 2

        group_definitions = None
        if args.group_config:
            group_config_path = args.group_config
            if not group_config_path.is_absolute():
                group_config_path = repo_root / group_config_path
            try:
                group_definitions = load_group_definitions(group_config_path)
            except GroupConfigError as exc:
                print(f"Failed to load group config: {exc}", file=sys.stderr)
                return 2

        parse_result, tracked_files = load_entries_and_repo_files(
            codeowners_path,
            repo_root,
            group_definitions=group_definitions,
        )

        targets = determine_targets(parse_result.entries, tracked_files, args.paths)
        if not targets:
            return 0

        paths_for_log = targets if args.restrict_to_targets else None
        index = build_activity_index(
            repo_root=repo_root,
            include_merges=args.include_merges,
            since=args.since,
            paths=paths_for_log,
        )

        suggestions = suggest_owners_for_paths(
            index,
            targets,
            limit=args.limit,
            min_commits=args.min_commits,
        )

        for suggestion in suggestions:
            header_type = "Directory" if suggestion.is_directory else "File"
            print(f"{header_type}: {suggestion.path}")
            if not suggestion.candidates:
                print("  No contributors found in Git history for this target.")
                continue
            total = suggestion.total_commits or 0
            for candidate in suggestion.candidates:
                share = f"{candidate.share * 100:5.1f}%" if suggestion.total_commits else "  0.0%"
                print(f"  - {candidate.identity:<30} {candidate.commits:4d} commits ({share})")
            if args.min_commits > 1:
                print(f"    (Only contributors with >= {args.min_commits} commits are listed.)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
