from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codeowners_tools.analysis import find_unowned_paths, find_unused_entries, load_entries_and_repo_files
from codeowners_tools.git_activity import build_activity_index, suggest_owners_for_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a combined CODEOWNERS audit and print actionable follow-ups.",
    )
    parser.add_argument(
        "--repo-root",
        default=Path.cwd(),
        type=Path,
        help="Path to the repository root (defaults to current working directory).",
    )
    parser.add_argument(
        "--codeowners",
        default="CODEOWNERS",
        type=Path,
        help="Path to the CODEOWNERS file relative to the repository root.",
    )
    parser.add_argument(
        "--suggest-limit",
        type=int,
        default=3,
        help="Maximum number of suggested owners per target (default: 3).",
    )
    parser.add_argument(
        "--min-commits",
        type=int,
        default=1,
        help="Minimum number of commits required to list a contributor (default: 1).",
    )
    parser.add_argument(
        "--since",
        help="Optional Git --since range, e.g. '1 year ago'.",
    )
    parser.add_argument(
        "--include-merges",
        action="store_true",
        help="Include merge commits when mining Git history for suggestions.",
    )
    parser.add_argument(
        "--max-unowned",
        type=int,
        default=20,
        help="Show at most N uncovered paths in detail (default: 20).",
    )
    parser.add_argument(
        "--fail-on-issues",
        action="store_true",
        help="Exit with status 1 when unused patterns or uncovered files are found.",
    )
    return parser.parse_args()


def _resolve_paths(unowned: List[str], limit: int) -> List[str]:
    if limit <= 0:
        return []
    return unowned[:limit]


def main() -> int:
    args = parse_args()

    repo_root = args.repo_root.resolve()
    codeowners_path = args.codeowners
    if not codeowners_path.is_absolute():
        codeowners_path = repo_root / codeowners_path

    if not codeowners_path.exists():
        print(f"CODEOWNERS file not found: {codeowners_path}", file=sys.stderr)
        return 2

    entries, tracked_files = load_entries_and_repo_files(codeowners_path, repo_root)

    unused = find_unused_entries(entries, tracked_files)
    unowned = find_unowned_paths(entries, tracked_files)

    print("=== CODEOWNERS Audit ===")
    print(f"Repository root: {repo_root}")
    print(f"CODEOWNERS file: {codeowners_path}")
    print()

    if unused:
        print(f"Unused patterns: {len(unused)}")
        for entry in unused[: args.max_unowned]:
            owners = " ".join(entry.owners)
            location = f"{entry.source.name}:{entry.line_number}"
            print(f"  - {entry.pattern:<30} owners: {owners:<30} ({location})")
        if len(unused) > args.max_unowned:
            print(f"  ... and {len(unused) - args.max_unowned} more")
    else:
        print("Unused patterns: none ✅")
    print()

    if unowned:
        print(f"Uncovered files: {len(unowned)}")
        sample = _resolve_paths(unowned, args.max_unowned)
        for path in sample:
            print(f"  - {path}")
        if len(unowned) > len(sample):
            print(f"  ... and {len(unowned) - len(sample)} more")

        top_dirs = Counter(path.split("/", 1)[0] for path in unowned)
        print("\nTop directories missing owners:")
        for directory, count in top_dirs.most_common(10):
            print(f"  - {directory}: {count} file(s)")

        print("\nSuggested owners (based on Git activity):")
        targets = sample or unowned[: args.max_unowned]
        index = build_activity_index(
            repo_root=repo_root,
            include_merges=args.include_merges,
            since=args.since,
            paths=targets,
        )
        suggestions = suggest_owners_for_paths(
            index,
            targets,
            limit=args.suggest_limit,
            min_commits=args.min_commits,
        )
        for item in suggestions:
            header = "Directory" if item.is_directory else "File"
            print(f"  {header}: {item.path}")
            if not item.candidates:
                print("    (no contributors with enough commits)")
                continue
            for candidate in item.candidates:
                share = f"{candidate.share * 100:5.1f}%" if item.total_commits else "  0.0%"
                print(f"    - {candidate.identity:<30} {candidate.commits:4d} commits ({share})")
            if args.min_commits > 1:
                print(f"      * contributors need >= {args.min_commits} commits")
    else:
        print("Uncovered files: none ✅")

    print("\nNext actions:")
    if unused or unowned:
        if unused:
            print("  - prune or fix unused patterns above")
        if unowned:
            print("  - add CODEOWNERS entries for uncovered paths")
            print("  - reach out to suggested owners to confirm responsibility")
    else:
        print("  - CODEOWNERS file covers all tracked files and patterns in use 🎉")

    if args.fail_on_issues and (unused or unowned):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
