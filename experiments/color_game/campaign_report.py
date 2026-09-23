"""Small campaign reports built from the saved manifest, never raw transcripts."""
from __future__ import annotations

import copy
import csv
import html
import io
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .config import SETTINGS

_ACTIVE = {"running", "interrupting", "starting"}


def _count(value):
    return value if type(value) is int and value >= 0 else 0


def _optional_count(*values):
    counts = [v for v in values if type(v) is int and v >= 0]
    return max(counts) if counts else None


def _link(path, directory, original_directory):
    """Rebase saved absolute paths so copied reports still work offline."""
    if not isinstance(path, str) or not path:
        return None
    candidate = Path(path)
    if candidate.is_absolute():
        roots = [directory]
        if isinstance(original_directory, str) and original_directory:
            roots.insert(0, Path(original_directory))
        for root in roots:
            try:
                candidate = candidate.relative_to(root)
                break
            except ValueError:
                continue
        else:
            return None
    if ".." in candidate.parts or ":" in str(candidate) or not candidate.parts:
        return None
    return quote(candidate.as_posix(), safe="/")


def _row(outcome, directory, original_directory):
    summary = outcome.get("summary") or {}
    progress = outcome.get("progress") or {}
    error = outcome.get("error") or {}
    error = {key: error[key] for key in ("type", "category", "status_code") if key in error}
    cost = summary.get("cost")
    if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
        cost = None
    links = {key: link for key in ("html", "json", "jsonl", "plan")
             if (link := _link((outcome.get("artifact_paths") or {}).get(key),
                               directory, original_directory)) is not None}
    valid_rounds = _count(summary.get("valid_rounds"))
    valid_matches = _count(summary.get("valid_matches"))
    return {
        "job_id": outcome.get("job_id", ""), "setting": outcome.get("setting", ""),
        "rollout_index": outcome.get("rollout_index"), "status": outcome.get("status", "unknown"),
        "planned_rounds": _count((outcome.get("config") or {}).get("rounds")),
        "recorded_rounds": _count(summary.get("recorded_rounds")),
        "rounds_completed": _count(progress.get("rounds_completed")),
        "round_index": progress.get("round_index"), "last_event": progress.get("last_event", ""),
        "actions_completed": max(_count(progress.get("actions_completed")),
                                 _count(summary.get("total_actions"))),
        "requests_started": _optional_count(progress.get("requests_started"), summary.get("requests_started")),
        "model_api_errors": _optional_count(progress.get("model_api_errors"), summary.get("model_api_errors")),
        "matched": _count(summary.get("matched")),
        "valid_rounds": valid_rounds, "valid_matches": valid_matches,
        "valid_accuracy": valid_matches / valid_rounds if valid_rounds else None,
        "errors": _count(summary.get("errors")),
        "infrastructure_errors": _count(summary.get("infrastructure_errors")),
        "missing_choices": _count(summary.get("missing_choices")),
        "pending_responses": _count(summary.get("pending_responses")),
        "reported_cost_usd": cost, "error": error, "links": links,
    }


def _aggregate(rows):
    result = {key: sum(row[key] for row in rows) for key in (
        "planned_rounds", "recorded_rounds", "rounds_completed", "actions_completed", "matched",
        "valid_rounds", "valid_matches", "errors", "infrastructure_errors", "missing_choices", "pending_responses")}
    for metric in ("requests_started", "model_api_errors"):
        known = [row[metric] for row in rows if row[metric] is not None]
        result[metric] = sum(known) if known else None
        result[f"rollouts_with_{metric}"] = len(known)
    result.update(
        planned_rollouts=len(rows),
        queued_rollouts=sum(row["status"] == "queued" for row in rows),
        running_rollouts=sum(row["status"] == "running" for row in rows),
        completed_rollouts=sum(str(row["status"]).startswith("complete") for row in rows),
        failed_rollouts=sum(row["status"] == "failed" for row in rows),
        interrupted_rollouts=sum(row["status"] == "interrupted" for row in rows),
        rollouts_with_errors=sum(bool(row["error"] or row["errors"] or row["infrastructure_errors"])
                                 for row in rows),
        valid_accuracy=result["valid_matches"] / result["valid_rounds"] if result["valid_rounds"] else None,
    )
    costs = [row["reported_cost_usd"] for row in rows if row["reported_cost_usd"] is not None]
    result["reported_cost_usd"] = sum(costs) if costs else None
    result["rollouts_with_reported_cost"] = len(costs)
    result["partial"] = (result["valid_rounds"] < result["planned_rounds"]
                         or result["completed_rollouts"] < result["planned_rollouts"])
    return result


def _atomic_text(path, text):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _e(value):
    return html.escape(str(value), quote=True)


def _accuracy(row):
    accuracy = row["valid_accuracy"]
    return "—" if accuracy is None else f"{accuracy:.1%} ({row['valid_matches']}/{row['valid_rounds']})"


def _value(value):
    return "—" if value is None else _e(value)


def _render(progress):
    summary = progress["summary"]
    live = progress["status"] in _ACTIVE
    refresh = '<meta http-equiv="refresh" content="5">' if live else ""
    parts = [f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{refresh}
<title>Color game campaign</title><style>
body{{font:15px system-ui,sans-serif;max-width:1500px;margin:2rem auto;padding:0 1rem;color:#17212b;background:#fafbfc}}
h1{{font-size:1.6rem}}table{{border-collapse:collapse;width:100%;background:white;margin:1rem 0}}
th,td{{padding:.55rem .65rem;border-bottom:1px solid #dce1e5;text-align:left;vertical-align:top}}
th{{background:#eef2f5;white-space:nowrap}}a{{color:#0645ad}}.scroll{{overflow-x:auto}}.note{{padding:1rem;background:#fff5d6}}
.small{{font-size:.88rem;color:#485666}}code{{overflow-wrap:anywhere}}td{{font-variant-numeric:tabular-nums}}
</style></head><body><h1>Color game campaign</h1>
<p><strong>Status: {_e(progress['status'])}</strong> · Model: {_e(progress['model'])}<br>
Campaign: <code>{_e(progress['campaign_id'])}</code><br>
Manifest updated: {_e(progress['manifest_updated_utc'])}</p>''']
    if summary["partial"]:
        parts.append('<p class="note"><strong>Partial results.</strong> Scores use saved, completed rounds. '
                     'Valid accuracy excludes rounds that the game marked invalid. '
                     'Matches / planned includes rounds that have not run. It is not the final accuracy.</p>')
    else:
        parts.append('<p>Valid accuracy uses all completed rounds that the game marked valid.</p>')
    parts.append(f'<p>{summary["completed_rollouts"]}/{summary["planned_rollouts"]} rollouts complete; '
                 f'{summary["running_rollouts"]} running; {summary["queued_rollouts"]} queued; '
                 f'{summary["failed_rollouts"]} failed; {summary["interrupted_rollouts"]} interrupted. '
                 f'Maximum parallel rollouts: {_e(progress["max_parallel_rollouts"])}.</p>')
    parts.append('<p><a href="campaign.json">Campaign manifest</a> · <a href="progress.json">Progress JSON</a> · '
                 '<a href="rollouts.csv">Rollout CSV</a></p><h2>Settings</h2><div class="scroll"><table><thead><tr>')
    headers = ("Setting", "Complete / planned", "Running", "Queued", "Failed", "Interrupted", "Valid accuracy",
               "Matches / planned rounds", "Actions observed", "Requests observed", "API errors observed",
               "Saved infrastructure errors", "Saved missing choices")
    parts.extend(f"<th>{_e(header)}</th>" for header in headers)
    parts.append("</tr></thead><tbody>")
    for setting, row in progress["settings"].items():
        values = (setting, f'{row["completed_rollouts"]}/{row["planned_rollouts"]}', row["running_rollouts"],
                  row["queued_rollouts"], row["failed_rollouts"], row["interrupted_rollouts"], _accuracy(row),
                  f'{row["matched"]}/{row["planned_rounds"]}', row["actions_completed"], row["requests_started"],
                  row["model_api_errors"], row["infrastructure_errors"], row["missing_choices"])
        parts.append("<tr>" + "".join(f"<td>{_value(value)}</td>" for value in values) + "</tr>")
    parts.append('</tbody></table></div><p class="small">Live counts are observed progress. They can lag behind saved '
                 'events. Settled rollouts use exact saved counts. A dash means the count is unavailable. '
                 'Saved infrastructure errors and missing choices are reported when each rollout settles. '
                 'Provider costs are recorded only when the provider returns them; missing costs are not zero.</p>')
    parts.append('<h2>Rollouts</h2><div class="scroll"><table><thead><tr>')
    headers = ("Job", "Status", "Rounds complete", "Actions", "Requests", "API errors", "Valid accuracy",
               "Saved errors / missing choices", "Last event", "Transcripts")
    parts.extend(f"<th>{_e(header)}</th>" for header in headers)
    parts.append("</tr></thead><tbody>")
    for row in progress["rollouts"]:
        values = (row["job_id"], row["status"], f'{row["rounds_completed"]}/{row["planned_rounds"]}',
                  row["actions_completed"], row["requests_started"], row["model_api_errors"], _accuracy(row),
                  f'{row["infrastructure_errors"]} / {row["missing_choices"]}', row["last_event"])
        parts.append("<tr>" + "".join(f"<td>{_value(value)}</td>" for value in values))
        links = " · ".join(f'<a href="{_e(path)}">{_e(name.upper())}</a>' for name, path in row["links"].items())
        parts.append(f'<td>{links or "Pending"}</td></tr>')
        if row["error"]:
            parts.append(f'<tr><td colspan="10"><code>{_e(json.dumps(row["error"], sort_keys=True))}</code></td></tr>')
    parts.append("</tbody></table></div>")
    if progress["callback_errors"]:
        parts.append(f'<p>Report or observer callback errors: {_e(progress["callback_errors"])}.</p>')
    if live:
        parts.append('<p class="small">This page refreshes every 5 seconds while the campaign runs.</p>')
    parts.append("</body></html>\n")
    return "".join(parts)


def _csv(progress):
    columns = ("job_id", "setting", "rollout_index", "status", "planned_rounds", "rounds_completed",
               "recorded_rounds", "valid_rounds", "valid_matches", "valid_accuracy", "matched",
               "actions_completed", "requests_started", "model_api_errors", "infrastructure_errors",
               "missing_choices", "pending_responses", "reported_cost_usd", "last_event", "html", "json", "jsonl", "plan")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for row in progress["rollouts"]:
        values = {key: row.get(key, row["links"].get(key, "")) for key in columns}
        # Keep arbitrary identifiers as data when users open the CSV in a spreadsheet.
        values = {key: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r"))
                  else value for key, value in values.items()}
        writer.writerow(values)
    return stream.getvalue()


def update_report(output_dir, campaign=None):
    """Write index.html, progress.json and rollouts.csv from one manifest snapshot.

    No rollout, event, plan, or provider response files are read. Each output is
    replaced atomically. The returned compact dict has ``summary``, ``settings``
    and ``rollouts`` fields. Supplying a manifest does not mutate that object.
    """
    directory = Path(output_dir).expanduser().resolve()
    if campaign is None:
        campaign = json.loads((directory / "campaign.json").read_text(encoding="utf-8"))
    else:
        campaign = copy.deepcopy(campaign)
    if campaign.get("schema") != "color-game-campaign/v1":
        raise ValueError("Expected a color-game-campaign/v1 manifest")
    rows = [_row(outcome, directory, campaign.get("output_dir")) for outcome in campaign["outcomes"]]
    progress = {
        "schema": "color-game-campaign-progress/v1", "campaign_id": campaign.get("campaign_id", ""),
        "status": campaign.get("status", "unknown"),
        "model": (campaign.get("model") or {}).get("name", (campaign.get("model") or {}).get("model", "")),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_updated_utc": campaign.get("updated_utc", campaign.get("created_utc", "")),
        "max_parallel_rollouts": campaign.get("max_parallel_rollouts"),
        "rollouts_per_setting": campaign.get("rollouts_per_setting"),
        "selected_settings": campaign.get("selected_settings", list(SETTINGS)),
        "summary": _aggregate(rows),
        "settings": {setting: _aggregate([row for row in rows if row["setting"] == setting])
                     for setting in campaign.get("selected_settings", SETTINGS)},
        "rollouts": rows,
        "callback_errors": sum(_count(error.get("count", 1)) for error in campaign.get("callback_errors", [])),
        "notes": {
            "accuracy": "Valid accuracy uses saved completed rounds marked valid by the game.",
            "planned": "Matches / planned rounds is a partial count while any rounds are unfinished or invalid.",
            "progress": "Observed live counts can lag; settled counts come from the saved rollout summary.",
            "cost": "Provider-reported costs only; missing costs are not zero.",
        },
    }
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_text(directory / "progress.json", json.dumps(progress, ensure_ascii=False, indent=2) + "\n")
    _atomic_text(directory / "rollouts.csv", _csv(progress))
    _atomic_text(directory / "index.html", _render(progress))
    return progress
