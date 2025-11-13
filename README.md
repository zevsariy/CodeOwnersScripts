# CODEOWNERS Toolkit

Utility scripts that help grow CODEOWNERS coverage and ownership culture in Git repositories.

## Scripts

- `scripts/check_unused_patterns.py` - report CODEOWNERS patterns that never match tracked files (supports local path or `--repo-url`).
- `scripts/find_unowned_paths.py` - list tracked files that have no owner assignment (works with remote clones too).
- `scripts/suggest_owners.py` - guess potential owners based on Git activity.
- `scripts/run_audit.py` - run an end-to-end audit showing unused masks, uncovered files, and guardrail check results in one pass.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
# install extra dependencies here if needed
```

## Usage

By default the scripts use the current directory, but you can point them at another repo with `--repo-root` or supply `--repo-url` to clone a remote branch on the fly. Pair `--codeowners` when the file lives outside the root (for example `.github/CODEOWNERS`). Use `--group-config` to point at a group definition file when your CODEOWNERS uses `@@Group` aliases.

### Check unused patterns

```powershell
python scripts\check_unused_patterns.py --fail-on-unused
```

### Find uncovered files

```powershell
python scripts\find_unowned_paths.py --group-by-directory
```

### Suggest owners

```powershell
python scripts\suggest_owners.py --limit 5 --since "1 year ago"
```

You can analyze specific paths:

```powershell
python scripts\suggest_owners.py --paths src/feature/ README.md
```

To analyze a remote repository in one-off mode:

```powershell
python scripts\run_audit.py --repo-url https://github.com/org/repo.git --branch main --codeowners .github/CODEOWNERS
```

### All-in-one audit

```powershell
python scripts\run_audit.py --repo-root C:\path\to\repo --codeowners CODEOWNERS --group-config groups.txt --fail-on-issues
```

Change `--max-unowned` to trim the detailed list and apply `--since` when you only care about recent activity. Guardrail directives written as `Check (@@Group >= 2)` are evaluated and surfaced in the report (provide group membership via `--group-config`).

### Extended syntax support

Besides the standard CODEOWNERS grammar, the parser understands:

- Group aliases referenced as `@@GroupName`, optionally defined inline (`@@GroupName: @alice @bob`) or in an external config file (JSON, YAML, or simple `key: value` text).
- Nested groups (`@@Backend` can expand to other `@@` aliases).
- Guardrail directives in block sections, e.g.

	```text
	#Testing any
	{
			/.anyfile @@Platform_ANY
			/.dev* @@New_Group
			Check (@@Platform_ANY >= 2)
			Check (@@New_Group >=1)
	}
	```

Group definitions file examples:

```text
@@Platform_ANY: @alice @bob @carol
@@New_Group = @dave @erin
```

or

```json
{
	"Platform_ANY": ["@alice", "@bob", "@carol"],
	"New_Group": "@dave @erin"
}
```

### Quick smoke-test

If you just dropped the scripts into a new repository, run them in "dry" mode to make sure the wiring works:

```powershell
python scripts\check_unused_patterns.py --codeowners CODEOWNERS --repo-root C:\path\to\repo --help
python scripts\find_unowned_paths.py --codeowners CODEOWNERS --repo-root C:\path\to\repo --limit 10
python scripts\suggest_owners.py --codeowners CODEOWNERS --repo-root C:\path\to\repo --paths src/ README.md --limit 5 --restrict-to-targets
```

## Tests

Install dev requirements once, then run the test-suite:

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

The tests exercise pattern matching, coverage reporting, and suggestion ranking heuristics.

## CI integration

Below is a minimal GitHub Actions workflow that runs the tests and fails the build when uncovered files exist. Adapt the `CODEOWNERS` path and additional steps to match your repository layout.

```yaml
name: codeowners-audit

on:
  pull_request:
  push:

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python -m pip install --upgrade pip
      - run: pip install -r requirements-dev.txt
      - run: python -m pytest
      - run: python scripts/check_unused_patterns.py --fail-on-unused
      - run: python scripts/find_unowned_paths.py --fail-on-unowned
```

For very large repositories, add `--restrict-to-targets` and `--since` flags to `suggest_owners.py` to keep execution time predictable.
