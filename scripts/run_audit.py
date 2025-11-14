from __future__ import annotations

import argparse
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codeowners_tools.audit import generate_audit
from codeowners_tools.remote import prepare_repository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a combined CODEOWNERS audit and print actionable follow-ups.",
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


def _format_guardrail_status(status: str) -> str:
    if status == "pass":
        return "pass"
    if status == "fail":
        return "FAIL"
    if status == "missing-group":
        return "unresolved group"
    if status == "unsupported-operator":
        return "unsupported operator"
    if status == "unparsed":
        return "unparsed"
    return status


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

        audit = generate_audit(
            repo_root=repo_root,
            codeowners_path=codeowners_path,
            repo_url=args.repo_url,
            branch=args.branch,
            max_unowned=args.max_unowned,
            suggest_limit=args.suggest_limit,
            min_commits=args.min_commits,
            include_merges=args.include_merges,
            since=args.since,
        )
        print("=== CODEOWNERS Audit ===")
        print(f"Repository root: {repo_root}")
        if args.repo_url:
            ref = args.branch or "default"
            print(f"Source: {args.repo_url} (branch: {ref})")
        print(f"CODEOWNERS file: {codeowners_path}")
        print(f"Tracked files: {audit.tracked_files_count}")
        print()

        if audit.unused_entries:
            print(f"Unused patterns: {audit.unused_total}")
            for entry in audit.unused_entries[: args.max_unowned]:
                owners = " ".join(entry.owners)
                location = f"{entry.source.name}:{entry.line_number}"
                print(f"  - {entry.pattern:<30} owners: {owners:<30} ({location})")
            if audit.unused_total > args.max_unowned:
                print(f"  ... and {audit.unused_total - args.max_unowned} more")
        else:
            print("Unused patterns: none ✅")
        print()

        if audit.guardrails:
            print("Guardrail checks:")
            for guardrail in audit.guardrails:
                label = _format_guardrail_status(guardrail.status)
                member_text = "?" if guardrail.member_count is None else str(guardrail.member_count)
                print(
                    f"  - {guardrail.directive.raw} -> {label} (members: {member_text}, target: {guardrail.directive.threshold})"
                )
            print()

        if audit.unowned_paths:
            print(f"Uncovered files: {audit.unowned_total}")
            sample = _resolve_paths(audit.unowned_paths, args.max_unowned)
            for path in sample:
                print(f"  - {path}")
            if audit.unowned_total > len(sample):
                print(f"  ... and {audit.unowned_total - len(sample)} more")

            if audit.top_directories:
                print("\nTop directories missing owners:")
                for directory, count in audit.top_directories:
                    print(f"  - {directory}: {count} file(s)")

            print("\nSuggested owners (based on Git activity):")
            if audit.suggestions:
                for item in audit.suggestions:
                    header = "Directory" if item.is_directory else "File"
                    print(f"  {header}: {item.path}")
                    if not item.candidates:
                        print("    (no contributors with enough commits)")
                        continue
                    for candidate in item.candidates:
                        share = f"{candidate.share * 100:5.1f}%" if item.total_commits else "  0.0%"
                        print(
                            f"    - {candidate.identity:<30} {candidate.commits:4d} commits ({share})"
                        )
                    if args.min_commits > 1:
                        print(f"      * contributors need >= {args.min_commits} commits")
            else:
                if not audit.suggestion_targets:
                    print("  (no suggestion targets)")
                else:
                    print("  (no contributors with enough commits)")
        else:
            print("Uncovered files: none ✅")

        print("\nNext actions:")
        if audit.has_issues:
            if audit.unused_entries:
                print("  - prune or fix unused patterns above")
            if audit.unowned_paths:
                print("  - add CODEOWNERS entries for uncovered paths")
                print("  - reach out to suggested owners to confirm responsibility")
            if audit.has_guardrail_failures:
                print("  - resolve guardrail checks that are failing or unresolved")
        else:
            if audit.guardrails:
                if audit.has_guardrail_failures:
                    print("  - review guardrail check warnings")
                else:
                    print("  - all guardrail checks satisfied 🛡️")
            else:
                print("  - CODEOWNERS file covers all tracked files and patterns in use 🎉")

        if args.fail_on_issues and audit.has_issues:
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
