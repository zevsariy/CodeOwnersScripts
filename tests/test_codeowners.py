import subprocess
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

from codeowners_tools.audit import generate_audit
from codeowners_tools.codeowners import CodeownersEntry, parse_codeowners, resolve_owner_for_path
from codeowners_tools.analysis import find_unowned_paths, find_unused_entries
from codeowners_tools.git_activity import GitActivityIndex, suggest_owners_for_paths
from codeowners_tools.remote import prepare_repository


def make_entry(pattern: str, owners: list[str]) -> CodeownersEntry:
    return CodeownersEntry(pattern=pattern, owners=owners, line_number=1, source=Path("CODEOWNERS"))


def test_codeowners_matching_prefers_last_match() -> None:
    entries = [
        make_entry("*.py", ["@python-team"]),
        make_entry("src/app.py", ["@alice"]),
    ]
    match = resolve_owner_for_path(entries, "src/app.py")
    assert match is not None
    assert match.owners == ["@alice"]


def test_find_unused_entries_detects_unmatched_patterns() -> None:
    entries = [
        make_entry("*.py", ["@team"]),
        make_entry("docs/", ["@docs"]),
    ]
    repo_files = ["main.py", "README.md"]
    unused = find_unused_entries(entries, repo_files)
    assert len(unused) == 1
    assert unused[0].pattern == "docs/"


def test_find_unowned_paths_returns_sorted_file_list() -> None:
    entries = [
        make_entry("src/**", ["@backend"]),
        make_entry("README.md", ["@docs"]),
    ]
    repo_files = ["src/app.py", "docs/guide.md", "README.md"]
    unowned = find_unowned_paths(entries, repo_files)
    assert unowned == ["docs/guide.md"]


def test_suggest_owners_for_file_and_directory() -> None:
    index = GitActivityIndex(
        file_commits={
            "src/app.py": Counter({"Alice <alice@example.com>": 5, "Bob <bob@example.com>": 2}),
        },
        directory_commits={
            "": Counter({"Alice <alice@example.com>": 7, "Bob <bob@example.com>": 3}),
            "src": Counter({"Alice <alice@example.com>": 6, "Bob <bob@example.com>": 3}),
        },
    )

    suggestions = suggest_owners_for_paths(index, ["src/app.py", "src/"])
    assert len(suggestions) == 2

    file_suggestion = suggestions[0]
    assert file_suggestion.path == "src/app.py"
    assert not file_suggestion.is_directory
    assert file_suggestion.candidates[0].identity == "Alice <alice@example.com>"
    assert file_suggestion.candidates[0].commits == 5

    dir_suggestion = suggestions[1]
    assert dir_suggestion.path == "src/"
    assert dir_suggestion.is_directory
    assert dir_suggestion.candidates[0].identity == "Alice <alice@example.com>"
    assert dir_suggestion.candidates[0].commits == 6


def test_prepare_repository_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with ExitStack() as stack:
        result = prepare_repository(stack, None, None, None)
        assert result == tmp_path


def test_prepare_repository_returns_repo_root(tmp_path):
    with ExitStack() as stack:
        result = prepare_repository(stack, tmp_path, None, None)
        assert result == tmp_path.resolve()


def test_parse_codeowners_with_groups_and_checks(tmp_path):
    codeowners_content = """
@@Platform_ANY: @alice @bob
#Testing any
{
    /.anyfile @@Platform_ANY
    /.dev* @@New_Group
    Check (@@Platform_ANY >= 2)
    Check (@@New_Group >=1)
}
@@New_Group: @charlie
"""
    path = tmp_path / "CODEOWNERS"
    path.write_text(codeowners_content)

    result = parse_codeowners(path)

    assert len(result.entries) == 2
    first, second = result.entries
    assert first.pattern == "/.anyfile"
    assert first.owners == ["@alice", "@bob"]
    assert second.pattern == "/.dev*"
    assert second.owners == ["@charlie"]

    assert result.groups["Platform_ANY"] == ["@alice", "@bob"]
    assert result.groups["New_Group"] == ["@charlie"]

    assert len(result.checks) == 2
    assert result.checks[0].group == "Platform_ANY"
    assert result.checks[0].operator == ">="
    assert result.checks[0].threshold == 2
    assert result.checks[1].group == "New_Group"
    assert result.checks[1].threshold == 1


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True)


def test_generate_audit_reports_uncovered_and_guardrail_issues(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    codeowners_content = """
@@Platform: @alice @bob
CODEOWNERS @@Platform
*.py @@Platform
docs/** @docs
scripts/** @ops
Check (@@Platform >= 3)
"""
    (repo / "CODEOWNERS").write_text(codeowners_content.strip())
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('hi')\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "readme.md").write_text("docs\n")
    (repo / "infra").mkdir()
    (repo / "infra" / "server.tf").write_text("resource\n")

    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test User")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "Initial commit")

    audit = generate_audit(
        repo_root=repo,
        codeowners_path=repo / "CODEOWNERS",
        max_unowned=5,
        suggest_limit=2,
    )

    assert audit.unused_total == 1
    assert audit.unused_entries[0].pattern == "scripts/**"
    assert audit.unowned_paths == ["infra/server.tf"]
    assert audit.top_directories == [("infra", 1)]
    assert audit.has_guardrail_failures
    statuses = {status.status for status in audit.guardrails}
    assert "fail" in statuses
    assert audit.has_issues
    assert audit.suggestion_targets == ["infra/server.tf"]
    assert audit.suggestions
    first_suggestion = audit.suggestions[0]
    assert first_suggestion.path == "infra/server.tf"
    assert first_suggestion.candidates
    assert first_suggestion.candidates[0].identity == "Test User <test@example.com>"
