from __future__ import annotations

import argparse
import sys
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codeowners_tools.analysis import find_unowned_paths, load_entries_and_repo_files
from codeowners_tools.groups import GroupConfigError, load_group_definitions
from codeowners_tools.remote import prepare_repository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List tracked files that are not covered by CODEOWNERS.",
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
        "--limit",
        type=int,
        default=None,
        help="Only display the first N unowned paths.",
    )
    parser.add_argument(
        "--group-by-directory",
        action="store_true",
        help="Aggregate results by top-level directory instead of individual files.",
    )
    parser.add_argument(
        "--fail-on-unowned",
        action="store_true",
        help="Exit with status 1 when uncovered paths are found.",
    )
    return parser.parse_args()


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
        unowned = find_unowned_paths(parse_result.entries, tracked_files)

        if not unowned:
            print("All tracked files are covered by CODEOWNERS entries.")
            return 0

        print(f"Found {len(unowned)} unowned tracked paths.")

        if args.group_by_directory:
            buckets: Counter[str] = Counter()
            for path in unowned:
                parts = path.split("/", 1)
                top_level = parts[0] if parts else path
                buckets[top_level] += 1
            for directory, count in buckets.most_common():
                print(f"  - {directory}: {count} file(s) without owners")
        else:
            display = unowned if args.limit is None else unowned[: args.limit]
            for path in display:
                print(f"  - {path}")
            if args.limit is not None and len(unowned) > args.limit:
                print(f"  ... and {len(unowned) - args.limit} more")

        return 1 if args.fail_on_unowned else 0


if __name__ == "__main__":
    sys.exit(main())
