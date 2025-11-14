from __future__ import annotations

import argparse
import sys
from contextlib import ExitStack
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codeowners_tools.analysis import find_unused_entries, load_entries_and_repo_files
from codeowners_tools.remote import prepare_repository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report CODEOWNERS patterns that do not match any tracked files.",
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
        "--fail-on-unused",
        action="store_true",
        help="Exit with status 1 when unused patterns are found.",
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

        parse_result, tracked_files = load_entries_and_repo_files(
            codeowners_path,
            repo_root,
        )
        unused = find_unused_entries(parse_result.entries, tracked_files)

        if not unused:
            print("All CODEOWNERS patterns match at least one tracked file.")
            return 0

        print("Unused CODEOWNERS patterns detected:")
        for entry in unused:
            owners = " ".join(entry.owners)
            location = f"{entry.source}:{entry.line_number}"
            print(f"  - {entry.pattern:<30} | owners: {owners:<30} | at {location}")

        return 1 if args.fail_on_unused else 0


if __name__ == "__main__":
    sys.exit(main())
