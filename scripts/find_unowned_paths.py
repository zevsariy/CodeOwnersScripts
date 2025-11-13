from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codeowners_tools.analysis import find_unowned_paths, load_entries_and_repo_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List tracked files that are not covered by CODEOWNERS.",
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
    repo_root = args.repo_root.resolve()
    codeowners_path = args.codeowners
    if not codeowners_path.is_absolute():
        codeowners_path = repo_root / codeowners_path

    if not codeowners_path.exists():
        print(f"CODEOWNERS file not found: {codeowners_path}", file=sys.stderr)
        return 2

    entries, tracked_files = load_entries_and_repo_files(codeowners_path, repo_root)
    unowned = find_unowned_paths(entries, tracked_files)

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
