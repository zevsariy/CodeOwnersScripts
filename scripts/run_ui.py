from __future__ import annotations

import argparse
import html
import json
import sys
import threading
import urllib.parse
from contextlib import ExitStack
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional

from codeowners_tools.audit import AuditResult, generate_audit
from codeowners_tools.groups import GroupConfigError, load_group_definitions
from codeowners_tools.remote import prepare_repository


def _default_form_values() -> Dict[str, str]:
    return {
        "repo_root": "",
        "repo_url": "",
        "branch": "",
        "codeowners": "CODEOWNERS",
        "group_config": "",
        "max_unowned": "20",
        "suggest_limit": "3",
        "min_commits": "1",
        "since": "",
        "include_merges": "",
    }


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _render_form(values: Dict[str, str]) -> str:
    def checked(field: str) -> str:
        return "checked" if values.get(field) else ""

    return f"""
    <form method=\"post\" class=\"form\">
      <fieldset>
        <legend>Repository</legend>
        <label>Local repo root<br><input name=\"repo_root\" type=\"text\" value=\"{_escape(values.get('repo_root', ''))}\" placeholder=\"C:/path/to/repo\"></label>
        <label>Remote repo URL<br><input name=\"repo_url\" type=\"text\" value=\"{_escape(values.get('repo_url', ''))}\" placeholder=\"https://github.com/org/repo.git\"></label>
        <label>Branch/ref (when cloning)<br><input name=\"branch\" type=\"text\" value=\"{_escape(values.get('branch', ''))}\"></label>
      </fieldset>
      <fieldset>
        <legend>Paths</legend>
        <label>CODEOWNERS path<br><input name=\"codeowners\" type=\"text\" required value=\"{_escape(values.get('codeowners', 'CODEOWNERS'))}\"></label>
        <label>Group config path<br><input name=\"group_config\" type=\"text\" value=\"{_escape(values.get('group_config', ''))}\" placeholder=\"optional\"></label>
      </fieldset>
      <fieldset>
        <legend>Suggestions</legend>
        <label>Max uncovered paths<br><input name=\"max_unowned\" type=\"number\" min=\"0\" value=\"{_escape(values.get('max_unowned', '20'))}\"></label>
        <label>Suggested owners per target<br><input name=\"suggest_limit\" type=\"number\" min=\"1\" value=\"{_escape(values.get('suggest_limit', '3'))}\"></label>
        <label>Minimum commits per owner<br><input name=\"min_commits\" type=\"number\" min=\"1\" value=\"{_escape(values.get('min_commits', '1'))}\"></label>
        <label>Git --since filter<br><input name=\"since\" type=\"text\" value=\"{_escape(values.get('since', ''))}\" placeholder=\"e.g. 90 days ago\"></label>
        <label class=\"checkbox\"><input type=\"checkbox\" name=\"include_merges\" {checked('include_merges')}>Include merge commits</label>
      </fieldset>
      <div class=\"actions\">
        <button type=\"submit\">Run audit</button>
      </div>
    </form>
    """


def _render_guardrails(audit: AuditResult) -> str:
    if not audit.guardrails:
        return "<p>No guardrail directives found.</p>"
    rows = []
    for guardrail in audit.guardrails:
        status = guardrail.status
        member_count = guardrail.member_count if guardrail.member_count is not None else "&mdash;"
        rows.append(
            f"<tr><td>{_escape(guardrail.directive.raw)}</td><td>{_escape(status)}</td><td>{_escape(member_count)}</td><td>{_escape(guardrail.directive.threshold)}</td></tr>"
        )
    return """<table class=\"results-table\"><thead><tr><th>Check</th><th>Status</th><th>Members</th><th>Threshold</th></tr></thead><tbody>""" + "".join(rows) + "</tbody></table>"


def _render_unused(audit: AuditResult) -> str:
    if not audit.unused_entries:
        return "<p>No unused patterns.</p>"
    rows = []
    for entry in audit.unused_entries:
        owners = " ".join(entry.owners)
        rows.append(
            f"<tr><td>{_escape(entry.pattern)}</td><td>{_escape(owners)}</td><td>{_escape(entry.source.name)}</td><td>{_escape(entry.line_number)}</td></tr>"
        )
    return """<table class=\"results-table\"><thead><tr><th>Pattern</th><th>Owners</th><th>File</th><th>Line</th></tr></thead><tbody>""" + "".join(rows) + "</tbody></table>"


def _render_unowned(audit: AuditResult) -> str:
    if not audit.unowned_paths:
        return "<p>All tracked paths are covered. ✅</p>"
    items = "".join(f"<li>{_escape(path)}</li>" for path in audit.unowned_paths)
    return f"<ol class=\"monospace\">{items}</ol>"


def _render_top_directories(audit: AuditResult) -> str:
    if not audit.top_directories:
        return "<p>No uncovered directories.</p>"
    rows = "".join(
        f"<tr><td>{_escape(directory)}</td><td>{_escape(count)}</td></tr>" for directory, count in audit.top_directories
    )
    return """<table class=\"results-table\"><thead><tr><th>Directory</th><th>Missing files</th></tr></thead><tbody>""" + rows + "</tbody></table>"


def _render_suggestions(audit: AuditResult) -> str:
    if not audit.suggestions:
        if audit.suggestion_targets:
            return "<p>No contributors met the minimum commit threshold.</p>"
        return "<p>No suggestion targets (all paths covered or limit set to zero).</p>"
    blocks = []
    for suggestion in audit.suggestions:
        header = "Directory" if suggestion.is_directory else "File"
        candidate_rows = []
        for candidate in suggestion.candidates:
            share = f"{candidate.share * 100:.1f}%" if suggestion.total_commits else "0.0%"
            candidate_rows.append(
                f"<tr><td>{_escape(candidate.identity)}</td><td>{_escape(candidate.commits)}</td><td>{_escape(share)}</td></tr>"
            )
        if not candidate_rows:
            candidate_rows.append("<tr><td colspan=3 class=\"muted\">No contributors with enough commits.</td></tr>")
        table = (
            """<table class=\"results-table\"><thead><tr><th>Identity</th><th>Commits</th><th>Share</th></tr></thead><tbody>"""
            + "".join(candidate_rows)
            + "</tbody></table>"
        )
        blocks.append(
            f"<section class=\"suggestion\"><h4>{_escape(header)}: {_escape(suggestion.path)} (total commits: {_escape(suggestion.total_commits)})</h4>{table}</section>"
        )
    return "".join(blocks)


def _render_result(audit: AuditResult) -> str:
    summary = {
        "Repo root": str(audit.repo_root),
        "CODEOWNERS": str(audit.codeowners_path),
        "Tracked files": audit.tracked_files_count,
        "Unused patterns": audit.unused_total,
        "Uncovered files": audit.unowned_total,
        "Guardrail issues": sum(1 for status in audit.guardrails if status.status != "pass"),
        "Duration (seconds)": f"{audit.duration_seconds:.3f}",
    }
    summary_rows = "".join(f"<tr><th>{_escape(key)}</th><td>{_escape(value)}</td></tr>" for key, value in summary.items())

    json_blob = json.dumps(audit.to_dict(), indent=2)

    return f"""
    <section class=\"card\">
      <h2>Summary</h2>
      <table class=\"summary-table\">{summary_rows}</table>
      <details><summary>Raw JSON</summary><pre>{_escape(json_blob)}</pre></details>
    </section>
    <section class=\"card\">
      <h2>Unused patterns</h2>
      {_render_unused(audit)}
    </section>
    <section class=\"card\">
      <h2>Guardrail checks</h2>
      {_render_guardrails(audit)}
    </section>
    <section class=\"card\">
      <h2>Uncovered paths</h2>
      {_render_unowned(audit)}
    </section>
    <section class=\"card\">
      <h2>Top directories missing owners</h2>
      {_render_top_directories(audit)}
    </section>
    <section class=\"card\">
      <h2>Suggested owners</h2>
      {_render_suggestions(audit)}
    </section>
    """


def _base_layout(content: str, values: Dict[str, str], error: Optional[str]) -> str:
    banner = "<div class=\"error\">" + _escape(error) + "</div>" if error else ""
    style = """
    <style>
      body { font-family: Segoe UI, Arial, sans-serif; margin: 0; background: #f6f7f9; color: #222; }
      header { background: #20232a; color: #fff; padding: 16px 32px; }
      main { margin: 24px auto 48px; max-width: 1100px; padding: 0 24px; }
      h1 { margin: 0; font-size: 24px; }
      form { display: grid; gap: 16px; margin-bottom: 24px; }
      fieldset { border: 1px solid #ccc; border-radius: 8px; padding: 16px; background: #fff; display: grid; gap: 12px; }
      legend { padding: 0 8px; font-weight: bold; }
      label { display: grid; gap: 4px; font-size: 14px; }
      input[type=text], input[type=number] { padding: 8px; border-radius: 4px; border: 1px solid #bbb; font-size: 14px; }
      .checkbox { align-items: center; grid-auto-flow: column; justify-content: start; }
      button { background: #0078d4; color: #fff; border: none; padding: 10px 16px; border-radius: 4px; font-size: 15px; cursor: pointer; }
      button:hover { background: #005ea8; }
      .actions { text-align: right; }
      .card { background: #fff; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px; box-shadow: 0 6px 16px rgba(32, 35, 42, 0.08); }
      table { border-collapse: collapse; width: 100%; }
      th, td { border: 1px solid #d4d7dd; padding: 6px 10px; text-align: left; }
      th { background: #f0f2f5; }
      .results-table { margin-top: 12px; }
      ol { padding-left: 20px; }
      .monospace { font-family: Consolas, 'Courier New', monospace; }
      .muted { color: #777; text-align: center; }
      .suggestion { margin-bottom: 18px; }
      .error { background: #fde7e9; color: #a61b2d; padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; }
      footer { text-align: center; padding: 12px; color: #555; font-size: 13px; }
      details summary { cursor: pointer; margin-top: 12px; }
    </style>
    """
    return f"""
    <!DOCTYPE html>
    <html lang=\"en\">
      <head>
        <meta charset=\"utf-8\">
        <title>CODEOWNERS Audit Dashboard</title>
        {style}
      </head>
      <body>
        <header>
          <h1>CODEOWNERS Audit Dashboard</h1>
        </header>
        <main>
          {banner}
          {_render_form(values)}
          {content}
        </main>
        <footer>Powered by codeowners_tools · generated locally</footer>
      </body>
    </html>
    """


def _run_audit_from_form(values: Dict[str, str]) -> AuditResult:
    repo_root_value = values.get("repo_root", "").strip()
    repo_root = Path(repo_root_value).expanduser() if repo_root_value else None

    repo_url = values.get("repo_url", "").strip() or None
    branch = values.get("branch", "").strip() or None

    codeowners_value = values.get("codeowners", "CODEOWNERS").strip()
    if not codeowners_value:
        raise ValueError("CODEOWNERS path is required")

    group_config_value = values.get("group_config", "").strip()
    include_merges = bool(values.get("include_merges"))
    since = values.get("since", "").strip() or None

    def _parse_int(field: str, default: int) -> int:
        raw = values.get(field, "")
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"Invalid integer for {field}: {raw}") from exc

    max_unowned = _parse_int("max_unowned", 20)
    suggest_limit = _parse_int("suggest_limit", 3)
    min_commits = _parse_int("min_commits", 1)

    with ExitStack() as stack:
        repo_path = prepare_repository(stack, repo_root, repo_url, branch)
        codeowners_path = Path(codeowners_value)
        if not codeowners_path.is_absolute():
            codeowners_path = repo_path / codeowners_path
        if not codeowners_path.exists():
            raise FileNotFoundError(f"CODEOWNERS file not found: {codeowners_path}")

        group_definitions = None
        if group_config_value:
            group_config_path = Path(group_config_value)
            if not group_config_path.is_absolute():
                group_config_path = repo_path / group_config_path
            try:
                group_definitions = load_group_definitions(group_config_path)
            except GroupConfigError as exc:
                raise ValueError(f"Failed to load group config: {exc}") from exc

        audit = generate_audit(
            repo_root=repo_path,
            codeowners_path=codeowners_path,
            repo_url=repo_url,
            branch=branch,
            group_definitions=group_definitions,
            max_unowned=max_unowned,
            suggest_limit=suggest_limit,
            min_commits=min_commits,
            include_merges=include_merges,
            since=since,
        )
    return audit


class AuditDashboardHandler(BaseHTTPRequestHandler):
    server_version = "CodeownersAuditDashboard/1.0"

    def _read_form(self) -> Dict[str, str]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        parsed = urllib.parse.parse_qs(raw)
        return {key: values[-1] for key, values in parsed.items()}

    def _write_response(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (method name enforced by BaseHTTPRequestHandler)
        values = _default_form_values()
        page = _base_layout("", values, None)
        self._write_response(page)

    def do_POST(self) -> None:  # noqa: N802
        values = _default_form_values()
        values.update(self._read_form())

        error: Optional[str] = None
        content = ""
        try:
            audit = _run_audit_from_form(values)
            content = _render_result(audit)
        except Exception as exc:  # broad catch to surface message in UI
            error = str(exc)
        page = _base_layout(content, values, error)
        self._write_response(page)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003 (shadow-builtin)
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch a simple CODEOWNERS audit web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the default web browser after the server starts.",
    )
    return parser.parse_args(argv)


def run_server(host: str, port: int, open_browser: bool = False) -> None:
    server = ThreadingHTTPServer((host, port), AuditDashboardHandler)

    if open_browser:
        try:
            import webbrowser

            threading.Timer(0.8, lambda: webbrowser.open(f"http://{host}:{port}/", new=2)).start()
        except Exception:  # pragma: no cover - best effort
            pass

    print(f"CODEOWNERS audit dashboard running at http://{host}:{port}/", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down...", file=sys.stderr)
        server.server_close()


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        run_server(args.host, args.port, args.open_browser)
        return 0
    except OSError as exc:
        print(f"Failed to start server: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
