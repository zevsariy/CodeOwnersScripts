from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codeowners_tools.analysis import find_unused_entries, load_entries_and_repo_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report CODEOWNERS patterns that do not match any tracked files.",
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
        "--fail-on-unused",
        action="store_true",
        help="Exit with status 1 when unused patterns are found.",
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
    unused = find_unused_entries(entries, tracked_files)

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
