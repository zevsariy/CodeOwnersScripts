from __future__ import annotations

import argparse
import html
import json
import sys
import threading
import urllib.parse
import uuid
from collections import Counter
from contextlib import ExitStack
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional

from codeowners_tools.audit import AuditResult, generate_audit
from codeowners_tools.groups import GroupConfigError, load_group_definitions
from codeowners_tools.remote import prepare_repository
from codeowners_tools.repo import list_tracked_files

EXPORT_LOCK = threading.Lock()
LATEST_EXPORT: Optional[Dict[str, str]] = None


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


def _looks_like_team(owner: str) -> bool:
    if owner.startswith("@@"):
        return True
    token = owner.lstrip("@")
    return "/" in token


def _top_categories(items: Counter[str], limit: int = 8) -> Dict[str, object]:
    ordered = sorted(items.items(), key=lambda kv: kv[1], reverse=True)
    if not ordered:
        return {"labels": [], "values": []}
    top = ordered[:limit]
    others_total = sum(value for _, value in ordered[limit:])
    labels = [label for label, _ in top]
    values = [value for _, value in top]
    if others_total:
        labels.append("Others")
        values.append(others_total)
    return {"labels": labels, "values": values}


def _prepare_chart_data(audit: AuditResult) -> Dict[str, object]:
    total_files = audit.tracked_files_count
    owned_files = max(total_files - audit.unowned_total, 0)
    coverage_percent = (owned_files / total_files * 100.0) if total_files else 0.0

    owner_counts: Counter[str] = Counter()
    try:
        repo_files = list_tracked_files(audit.repo_root)
    except Exception:  # pragma: no cover - git errors should not break UI visualisation
        repo_files = []

    unowned_set = set(audit.unowned_paths)
    entries = audit.parse_result.entries

    for path in repo_files:
        if path in unowned_set:
            continue
        match_entry = None
        for entry in entries:
            if entry.matches(path):
                match_entry = entry
        if match_entry is None:
            continue
        for owner in match_entry.owners:
            owner_counts[owner] += 1

    individual_counts = Counter({owner: count for owner, count in owner_counts.items() if not _looks_like_team(owner)})
    team_counts = Counter({owner: count for owner, count in owner_counts.items() if _looks_like_team(owner)})

    for group_name, members in audit.parse_result.groups.items():
        aggregated = sum(owner_counts.get(member, 0) for member in members)
        if aggregated:
            team_counts[f"@@{group_name}"] += aggregated

    top_dirs = audit.top_directories[:8]
    pattern_total = len(audit.parse_result.entries)
    used_patterns = max(pattern_total - audit.unused_total, 0)

    status_counts = Counter(status.status for status in audit.guardrails)
    if not status_counts:
        status_counts["none"] = 1

    return {
        "coverage": {
            "labels": ["Covered", "Unowned"],
            "values": [owned_files, audit.unowned_total],
            "percent": round(coverage_percent, 2),
            "total": total_files,
        },
        "owners": _top_categories(individual_counts),
        "teams": _top_categories(team_counts),
        "topDirectories": {
            "labels": [directory for directory, _ in top_dirs],
            "values": [count for _, count in top_dirs],
        },
        "patterns": {
            "labels": ["Used", "Unused"],
            "values": [used_patterns, audit.unused_total],
        },
        "guardrails": {
            "labels": list(status_counts.keys()),
            "values": list(status_counts.values()),
        },
    }


def _render_result(audit: AuditResult, export_token: Optional[str] = None, export_filename: Optional[str] = None) -> str:
    issues = audit.has_issues
    guardrail_issues = sum(1 for status in audit.guardrails if status.status != "pass")
    suggestion_count = len(audit.suggestions)

    metrics = [
        ("Tracked files", audit.tracked_files_count),
        ("Unused patterns", audit.unused_total),
        ("Uncovered files", audit.unowned_total),
        ("Guardrail issues", guardrail_issues),
        ("Suggestion targets", len(audit.suggestion_targets)),
        ("Generated suggestions", suggestion_count),
        ("Duration (s)", f"{audit.duration_seconds:.3f}"),
    ]
    metrics_html = "".join(
        f"<div class=\"metric-card\"><span class=\"metric-label\">{_escape(label)}</span><span class=\"metric-value\">{_escape(value)}</span></div>"
        for label, value in metrics
    )

    status_label = "All clear" if not issues else "Attention needed"
    status_class = "status-ok" if not issues else "status-warn"
    status_icon = "✅" if not issues else "⚠️"

    chart_data = _prepare_chart_data(audit)
    chart_json = json.dumps(chart_data)
    coverage_badge = (
        f"{chart_data['coverage']['percent']:.2f}%" if chart_data["coverage"].get("total") else "N/A"
    )

    json_blob = json.dumps(audit.to_dict(), indent=2)

    export_section = ""
    if export_token and export_filename:
        export_section = f"""
        <section class=\"card\">
            <header class=\"card-header\">
                <h2>Export CODEOWNERS</h2>
            </header>
            <p>Download a regenerated CODEOWNERS file using the custom @@@Group notation.</p>
            <form method=\"get\" action=\"/download\" class=\"export-form\">
                <input type=\"hidden\" name=\"token\" value=\"{_escape(export_token)}\">
                <button type=\"submit\">Download {_escape(export_filename)}</button>
            </form>
        </section>
        """

    chart_section = f"""
        <section class=\"card\">
            <header class=\"card-header\">
                <h2>Visual analytics</h2>
                <span class=\"chart-coverage\">Coverage now: {_escape(coverage_badge)}</span>
            </header>
            <div class=\"charts-grid\">
                <div class=\"chart-tile\">
                    <h3>Coverage</h3>
                    <canvas id=\"coverageChart\"></canvas>
                </div>
                <div class=\"chart-tile\">
                    <h3>Owners</h3>
                    <canvas id=\"ownerChart\"></canvas>
                </div>
                <div class=\"chart-tile\">
                    <h3>Teams &amp; Groups</h3>
                    <canvas id=\"teamChart\"></canvas>
                </div>
                <div class=\"chart-tile\">
                    <h3>Unowned Hotspots</h3>
                    <canvas id=\"directoriesChart\"></canvas>
                </div>
                <div class=\"chart-tile\">
                    <h3>Pattern Usage</h3>
                    <canvas id=\"patternChart\"></canvas>
                </div>
                <div class=\"chart-tile\">
                    <h3>Guardrails</h3>
                    <canvas id=\"guardrailChart\"></canvas>
                </div>
            </div>
            <script type=\"application/json\" id=\"chart-data\">{_escape(chart_json)}</script>
            <script>
            (function() {{
                if (typeof Chart === "undefined") {{
                    return;
                }}
                var payloadElement = document.getElementById("chart-data");
                if (!payloadElement) {{
                    return;
                }}
                var data;
                try {{
                    data = JSON.parse(payloadElement.textContent || "{{}}");
                }} catch (error) {{
                    console.warn("Failed to parse chart payload", error);
                    return;
                }}

                var palette = ["#0e7a4a", "#2fa86a", "#58c68a", "#8fe0ae", "#c6f1d2", "#1c5236", "#66bb6a", "#9ccc65"];

                function hasValues(values) {{
                    return Array.isArray(values) && values.some(function(value) {{ return Number(value) > 0; }});
                }}

                function buildDataset(labels, values, options) {{
                    return {{
                        labels: labels,
                        datasets: [Object.assign({{
                            data: values,
                            backgroundColor: palette.slice(0, Math.max(values.length, 1)),
                            borderWidth: 0,
                            hoverOffset: 6,
                        }}, options || {{}})],
                    }};
                }}

                function renderChart(config) {{
                    var canvas = document.getElementById(config.id);
                    if (!canvas) {{
                        return;
                    }}
                    var container = canvas.closest(".chart-tile");
                    if (config.skipWhen && config.skipWhen()) {{
                        if (container) {{
                            container.style.display = "none";
                        }}
                        return;
                    }}
                    new Chart(canvas.getContext("2d"), {{
                        type: config.type,
                        data: config.data(),
                        options: Object.assign({{
                            responsive: true,
                            maintainAspectRatio: false,
                            plugins: {{
                                legend: {{ display: config.showLegend !== false }},
                                tooltip: {{
                                    callbacks: {{
                                        label: function(context) {{
                                            var value = context.raw;
                                            if (config.valueLabel) {{
                                                return config.valueLabel(context.label, value);
                                            }}
                                            return context.label + ": " + value;
                                        }},
                                    }},
                                }},
                            }},
                            scales: config.scales,
                        }}, config.options || {{}}),
                    }});
                }}

                renderChart({{
                    id: "coverageChart",
                    type: "doughnut",
                    data: function() {{
                        return buildDataset(data.coverage.labels, data.coverage.values);
                    }},
                    valueLabel: function(label, value) {{
                        if (!data.coverage.total) {{
                            return label + ": 0";
                        }}
                        var percent = (value / data.coverage.total * 100).toFixed(1);
                        return label + ": " + value + " (" + percent + "%)";
                    }},
                    skipWhen: function() {{
                        return !hasValues(data.coverage.values);
                    }},
                }});

                renderChart({{
                    id: "ownerChart",
                    type: "bar",
                    data: function() {{
                        return buildDataset(data.owners.labels, data.owners.values, {{ backgroundColor: "#2fa86a" }});
                    }},
                    showLegend: false,
                    scales: {{
                        x: {{ ticks: {{ precision: 0 }} }},
                        y: {{ beginAtZero: true, ticks: {{ autoSkip: false }}, title: {{ display: true, text: "Owners" }} }},
                    }},
                    options: {{ indexAxis: "y" }},
                    skipWhen: function() {{
                        return !hasValues(data.owners.values);
                    }},
                }});

                renderChart({{
                    id: "teamChart",
                    type: "bar",
                    data: function() {{
                        return buildDataset(data.teams.labels, data.teams.values, {{ backgroundColor: "#58c68a" }});
                    }},
                    showLegend: false,
                    scales: {{
                        x: {{ ticks: {{ precision: 0 }} }},
                        y: {{ beginAtZero: true, ticks: {{ autoSkip: false }}, title: {{ display: true, text: "Teams" }} }},
                    }},
                    options: {{ indexAxis: "y" }},
                    skipWhen: function() {{
                        return !hasValues(data.teams.values);
                    }},
                }});

                renderChart({{
                    id: "directoriesChart",
                    type: "bar",
                    data: function() {{
                        return buildDataset(data.topDirectories.labels, data.topDirectories.values, {{ backgroundColor: "#8fe0ae" }});
                    }},
                    showLegend: false,
                    scales: {{
                        x: {{ beginAtZero: true, ticks: {{ precision: 0 }} }},
                        y: {{ ticks: {{ autoSkip: false }} }},
                    }},
                    options: {{ indexAxis: "y" }},
                    skipWhen: function() {{
                        return !hasValues(data.topDirectories.values);
                    }},
                }});

                renderChart({{
                    id: "patternChart",
                    type: "doughnut",
                    data: function() {{
                        return buildDataset(data.patterns.labels, data.patterns.values);
                    }},
                    skipWhen: function() {{
                        return !hasValues(data.patterns.values);
                    }},
                }});

                renderChart({{
                    id: "guardrailChart",
                    type: "doughnut",
                    data: function() {{
                        return buildDataset(data.guardrails.labels, data.guardrails.values);
                    }},
                    skipWhen: function() {{
                        return !hasValues(data.guardrails.values);
                    }},
                }});
            }})();
            </script>
        </section>
    """

    return f"""
        <section class=\"card\">
            <header class=\"card-header\">
                <h2>Audit status</h2>
                <span class=\"status-badge {status_class}\">{status_icon} {_escape(status_label)}</span>
            </header>
            <div class=\"metrics\">{metrics_html}</div>
        </section>
        {chart_section}
        <section class=\"card\">
            <header class=\"card-header\">
                <h2>Summary</h2>
            </header>
            <dl class=\"summary-list\">
                <div><dt>Repo root</dt><dd>{_escape(audit.repo_root)}</dd></div>
                <div><dt>CODEOWNERS</dt><dd>{_escape(audit.codeowners_path)}</dd></div>
                <div><dt>Remote source</dt><dd>{_escape(audit.repo_url or '—')}</dd></div>
                <div><dt>Branch</dt><dd>{_escape(audit.branch or '—')}</dd></div>
            </dl>
            <details class=\"raw-json\">
                <summary>Raw JSON result</summary>
                <pre>{_escape(json_blob)}</pre>
            </details>
        </section>
        <section class=\"card\">
            <details open>
                <summary>Unused patterns ({_escape(audit.unused_total)})</summary>
                {_render_unused(audit)}
            </details>
        </section>
        <section class=\"card\">
            <details open>
                <summary>Guardrail checks ({_escape(len(audit.guardrails))})</summary>
                {_render_guardrails(audit)}
            </details>
        </section>
        <section class=\"card\">
            <details open>
                <summary>Uncovered paths ({_escape(audit.unowned_total)})</summary>
                {_render_unowned(audit)}
            </details>
        </section>
        <section class=\"card\">
            <details>
                <summary>Top directories missing owners</summary>
                {_render_top_directories(audit)}
            </details>
        </section>
        <section class=\"card\">
            <details open>
                <summary>Suggested owners ({_escape(suggestion_count)})</summary>
                {_render_suggestions(audit)}
            </details>
        </section>
        {export_section}
    """


def _base_layout(content: str, values: Dict[str, str], error: Optional[str]) -> str:
    banner = "<div class=\"error\">" + _escape(error) + "</div>" if error else ""
    style = """
    <style>
      body { font-family: \"Segoe UI\", Arial, sans-serif; margin: 0; background: #f1f5f2; color: #1c2c21; }
      header { background: linear-gradient(135deg, #0e7a4a, #15a361); color: #fff; padding: 24px 32px; box-shadow: 0 4px 12px rgba(14, 122, 74, 0.35); }
      header h1 { font-size: 26px; font-weight: 700; letter-spacing: 0.5px; }
      main { margin: 24px auto 48px; max-width: 1100px; padding: 0 24px; }
      form { display: grid; gap: 18px; margin-bottom: 30px; }
      fieldset { border: 1px solid #9ccdad; border-radius: 12px; padding: 18px; background: #ffffff; display: grid; gap: 14px; box-shadow: 0 10px 24px rgba(16, 122, 74, 0.08); }
      legend { padding: 0 10px; font-weight: 600; color: #0e7a4a; }
      label { display: grid; gap: 6px; font-size: 13px; color: #274234; }
      input[type=text], input[type=number] { padding: 10px; border-radius: 6px; border: 1px solid #b4d9c2; font-size: 14px; transition: border-color 0.2s ease, box-shadow 0.2s ease; }
      input[type=text]:focus, input[type=number]:focus { border-color: #0e7a4a; box-shadow: 0 0 0 3px rgba(14, 122, 74, 0.15); outline: none; }
      .checkbox { align-items: center; grid-auto-flow: column; justify-content: start; }
      button { background: linear-gradient(135deg, #0e7a4a, #15a361); color: #fff; border: none; padding: 12px 18px; border-radius: 6px; font-size: 15px; cursor: pointer; font-weight: 600; box-shadow: 0 8px 16px rgba(14, 122, 74, 0.25); transition: transform 0.1s ease, box-shadow 0.2s ease; }
      button:hover { transform: translateY(-1px); box-shadow: 0 12px 22px rgba(14, 122, 74, 0.28); }
      button:active { transform: translateY(0); box-shadow: 0 6px 14px rgba(14, 122, 74, 0.25); }
      .actions { text-align: right; }
      .card { background: #ffffff; border-radius: 12px; padding: 18px 22px; margin-bottom: 26px; box-shadow: 0 12px 28px rgba(40, 80, 58, 0.12); border: 1px solid #dbe9df; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border: 1px solid #d0e4d7; padding: 8px 12px; text-align: left; }
      th { background: #e6f4ea; color: #254733; font-weight: 600; }
      .results-table { margin-top: 12px; }
      ol { padding-left: 20px; }
      .monospace { font-family: \"Fira Code\", Consolas, \"Courier New\", monospace; background: #0f1f17; color: #e3f5e9; padding: 10px; border-radius: 6px; }
      .muted { color: #6b8f78; text-align: center; }
      .suggestion { margin-bottom: 18px; }
      .error { background: #fde7e9; color: #a61b2d; padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; border: 1px solid #f5b5bf; }
      footer { text-align: center; padding: 16px; color: #2a4b3a; font-size: 13px; background: #d8ede1; border-top: 1px solid #b7d7c3; }
      footer strong { color: #0e7a4a; }
      details summary { cursor: pointer; margin-top: 12px; }
      .card-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; gap: 12px; }
      .card-header h2 { margin: 0; font-size: 18px; color: #123d2a; }
      .status-badge { display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border-radius: 18px; font-size: 13px; font-weight: 600; }
      .status-ok { background: #e4f3e8; color: #0d8a54; border: 1px solid #bde6cc; }
      .status-warn { background: #fff4ce; color: #8a6b0d; border: 1px solid #f0d886; }
      .metrics { display: grid; gap: 18px; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); }
      .metric-card { background: #f1fbf4; border: 1px solid #bfe5cb; border-radius: 10px; padding: 14px; display: grid; gap: 6px; box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.55); }
      .metric-label { font-size: 13px; color: #39624b; text-transform: uppercase; letter-spacing: 0.5px; }
      .metric-value { font-size: 22px; font-weight: 700; color: #0f5034; }
      .summary-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; margin: 0; padding: 0; }
      .summary-list div { background: #f7fbf8; border: 1px solid #cae5d2; border-radius: 10px; padding: 12px 14px; }
      .summary-list dt { font-size: 12px; text-transform: uppercase; color: #548362; margin-bottom: 4px; letter-spacing: 0.4px; }
      .summary-list dd { margin: 0; font-size: 14px; color: #1f3928; word-break: break-word; }
      .raw-json pre { background: #07361f; color: #c8ffe4; padding: 14px; border-radius: 8px; max-height: 320px; overflow: auto; font-size: 12px; border: 1px solid #0e7a4a; }
      .raw-json summary { font-weight: 600; color: #0e7a4a; }
      details { border-radius: 8px; padding: 4px 0; }
      details > summary { font-weight: 600; list-style: none; color: #134e32; }
      details > summary::marker { content: ""; }
      details > summary::after { content: "▸"; margin-left: 10px; transition: transform 0.2s ease; display: inline-block; color: inherit; }
      details[open] > summary::after { transform: rotate(90deg); }
      details > summary:hover { color: #0e7a4a; }
      .export-form { margin-top: 14px; }
      .export-form button { width: 100%; }
      .brand-badge { font-size: 13px; letter-spacing: 2px; text-transform: uppercase; color: #aee5c5; display: block; margin-bottom: 6px; font-weight: 600; }
      .charts-grid { display: grid; gap: 20px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
      .chart-tile { background: #f6fbf7; border: 1px solid #c9e7d2; border-radius: 12px; padding: 14px; box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6); display: grid; gap: 8px; min-height: 220px; }
      .chart-tile h3 { margin: 0; font-size: 14px; color: #1b5034; letter-spacing: 0.2px; }
      .chart-coverage { font-size: 13px; color: #0e7a4a; font-weight: 600; }
      canvas { width: 100%; height: 200px; }
    </style>
    """
    return f"""
    <!DOCTYPE html>
    <html lang=\"en\">
      <head>
        <meta charset=\"utf-8\">
        <title>CODEOWNERS Audit Dashboard</title>
        <script src=\"https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.min.js\"></script>
        {style}
      </head>
      <body>
        <header>
          <span class=\"brand-badge\">Scrum-Kitchen</span>
          <h1>CODEOWNERS Audit Dashboard</h1>
        </header>
        <main>
          {banner}
          {_render_form(values)}
          {content}
        </main>
        <footer>Powered by codeowners_tools · Crafted for Scrum-Kitchen · <strong>Sergey Tkachenko</strong></footer>
      </body>
    </html>
    """


def _register_export(audit: AuditResult) -> tuple[str, str]:
    global LATEST_EXPORT

    token = uuid.uuid4().hex
    filename_root = audit.codeowners_path.name or "CODEOWNERS"
    filename = f"{filename_root}.generated"
    content = audit.to_custom_codeowners()

    with EXPORT_LOCK:
        LATEST_EXPORT = {
            "token": token,
            "filename": filename,
            "content": content,
        }

    return token, filename


def _clear_export() -> None:
    global LATEST_EXPORT
    with EXPORT_LOCK:
        LATEST_EXPORT = None


def _fetch_export(token: Optional[str]) -> Optional[Dict[str, str]]:
    if not token:
        return None
    with EXPORT_LOCK:
        export = LATEST_EXPORT
    if export and export.get("token") == token:
        return export
    return None


def _parse_int(values: Dict[str, str], field: str, default: int) -> int:
    raw = values.get(field, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid integer for {field}: {raw}") from exc


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

    max_unowned = _parse_int(values, "max_unowned", 20)
    suggest_limit = _parse_int(values, "suggest_limit", 3)
    min_commits = _parse_int(values, "min_commits", 1)

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

        return generate_audit(
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


class AuditDashboardHandler(BaseHTTPRequestHandler):
    server_version = "CodeownersAuditDashboard/1.2"

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

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/download":
            params = urllib.parse.parse_qs(parsed.query)
            token = params.get("token", [None])[0]
            export = _fetch_export(token)
            if not export:
                self.send_error(HTTPStatus.NOT_FOUND, "No generated CODEOWNERS export available.")
                return
            data = export["content"].encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f"attachment; filename=\"{export['filename']}\"")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if parsed.path not in {"/", ""}:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/")
            self.end_headers()
            return

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
            token, filename = _register_export(audit)
            content = _render_result(audit, export_token=token, export_filename=filename)
        except Exception as exc:  # pragma: no cover - surface errors to UI
            error = str(exc)
            _clear_export()
        page = _base_layout(content, values, error)
        self._write_response(page)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
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
        except Exception:  # pragma: no cover - best effort only
            pass

    print(f"CODEOWNERS audit dashboard running at http://{host}:{port}/", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down...", file=sys.stderr)
    finally:
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
