"""Notebook and standalone HTML views of one color-game rollout.

The view is for the researcher. It can show both private histories; the game
runner never feeds this combined view to either player. No network is needed.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _escape(value: Any) -> str:
    return html.escape(str(value))


def _pre(value: Any) -> str:
    text = value if isinstance(value, str) else _json(value)
    return f"<pre>{_escape(text)}</pre>"


def _details(label: str, value: Any, *, opened: bool = False) -> str:
    if value is None:
        return ""
    return (f"<details{' open' if opened else ''}><summary>{_escape(label)}</summary>"
            f"{_pre(value)}</details>")


def _messages(messages: Any) -> str:
    if not isinstance(messages, list):
        return _pre(messages)
    pieces = []
    for message in messages:
        if not isinstance(message, dict):
            pieces.append(_pre(message))
            continue
        role = message.get("role", "message")
        content = message.get("content", message)
        pieces.append(f"<div class='message'><strong>{_escape(role)}</strong>{_pre(content)}</div>")
    return "".join(pieces)


def _usage(actions: list[dict]) -> dict:
    responses = [a.get("response") or {} for a in actions]
    usages = [r.get("usage") or {} for r in responses if isinstance(r, dict)]
    costs = [u["cost"] for u in usages
             if isinstance(u.get("cost"), (int, float)) and not isinstance(u.get("cost"), bool)]
    return {
        "saved_responses": sum(bool(r) for r in responses),
        "prompt_tokens": sum(u.get("prompt_tokens", u.get("input_tokens", 0)) or 0 for u in usages),
        "completion_tokens": sum(u.get("completion_tokens", u.get("output_tokens", 0)) or 0 for u in usages),
        "reported_cost_usd": sum(costs) if costs else None,
        "responses_with_reported_cost": len(costs),
    }


def _action_card(action: dict, ordinal: int) -> str:
    role = action.get("role", "unknown")
    step = action.get("step")
    tick = action.get("tick")
    phase = action.get("phase")
    response = action.get("response") or {}
    payload = action.get("action")
    error = action.get("error")
    title = f"{ordinal}. {str(role).title()}"
    if phase:
        title += f" · {phase}"
    if isinstance(step, int):
        title += f" · player action {step + 1}"
    if isinstance(tick, int) and not phase:
        title += f" · step {tick + 1}"
    if isinstance(payload, dict):
        title += f" · {payload.get('action', 'invalid action')}"
    result = action.get("result", action.get("observation"))
    reasoning = response.get("reasoning") if isinstance(response, dict) else None
    request = action.get("request_messages")
    request_system = action.get("request_system", action.get("system_prompt"))
    parts = [f"<article class='action {'has-error' if error else ''}'>",
             f"<h4>{_escape(title)}</h4>"]
    clock_labels = []
    if isinstance(action.get("round_elapsed_s"), (int, float)):
        clock_labels.append(f"Request at +{action['round_elapsed_s']:.3f}s")
    if isinstance(action.get("round_remaining_s"), (int, float)):
        clock_labels.append(f"Shared clock time left: {action['round_remaining_s']:.3f}s")
    if phase and isinstance(action.get("elapsed_s"), (int, float)):
        clock_labels.append(f"Response latency: {action['elapsed_s']:.3f}s")
    if clock_labels:
        parts.append(f"<p class='muted'>{_escape(' · '.join(clock_labels))}</p>")
    response_status = action.get("response_status")
    if response_status:
        parts.append(f"<p>Response status: <strong>{_escape(response_status)}</strong></p>")
    status_notes = {
        "pending": "The response is pending. Reload the saved rollout to check for a completed response.",
        "pending_late": "The phase is closed. This request was not applied. Its response may still arrive later.",
        "late": "This late response was saved but not applied. It does not change the recorded choices or score.",
        "received_unapplied": "This saved response was not applied. It does not change the recorded choices or score.",
    }
    if response_status in status_notes:
        parts.append(f"<p class='muted'>{status_notes[response_status]}</p>")
    parts.append(_pre(payload))
    if result is not None:
        parts += ["<strong>Private tool result</strong>", _pre(result)]
    if error:
        parts += ["<strong class='error'>Error</strong>", _pre(error)]
    if reasoning:
        parts += [_details("Returned reasoning", reasoning)]
    if request is not None:
        parts.append("<details><summary>Exact private input for this action</summary>")
        if request_system is not None:
            parts += ["<strong>system</strong>", _pre(request_system)]
        parts += [_messages(request), "</details>"]
    parts += [_details("Full saved response and usage", response)]
    parts += [_details("Full action record", action), "</article>"]
    return "".join(parts)


def _round_panel(record: dict, ordinal: int) -> str:
    alice = record.get("alice_color")
    bob = record.get("bob_color")
    match = record.get("match")
    label = "match" if match else "no match"
    if alice is None or bob is None:
        label = "missing choice"
    actions = record.get("actions") or []
    errors = record.get("errors") or []
    parts = [f"<section id='round-{ordinal}' class='round'>",
             f"<h3>Round {ordinal} · {_escape(label)}</h3>",
             "<p class='choices'>",
             f"Alice: <strong>{_escape(alice if alice is not None else '—')}</strong> · ",
             f"Bob: <strong>{_escape(bob if bob is not None else '—')}</strong> · ",
             f"{len(actions)} actions</p>"]
    if errors:
        parts += [_details("Round errors", errors, opened=True)]
    for index, action in enumerate(actions, 1):
        parts.append(_action_card(action, index))
    parts += [_details("Counter events in this round", record.get("counter_events")),
              _details("Full round metadata", {k: v for k, v in record.items() if k != "actions"}),
              "</section>"]
    return "".join(parts)


_STYLE = """
.color-game { color: #17263c; font: 15px/1.5 system-ui, sans-serif; max-width: 1180px;
  margin: 0 auto; padding: 20px; background: #fff; }
.color-game h1 { font-size: 27px; margin-bottom: 8px; }
.color-game h2 { font-size: 21px; margin-top: 28px; }
.color-game h3 { font-size: 19px; }
.color-game h4 { font-size: 16px; margin: 0 0 12px; }
.color-game p { margin: 8px 0 14px; }
.color-game .muted { color: #526174; }
.color-game table { border-collapse: collapse; width: 100%; margin: 14px 0; }
.color-game th, .color-game td { text-align: left; padding: 8px 12px;
  border-bottom: 1px solid #dce3eb; }
.color-game th { background: #eef3f8; }
.color-game pre { white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.5 ui-monospace,
  SFMono-Regular, Menlo, monospace; background: #f5f7fa; border-radius: 6px;
  padding: 10px; max-height: 550px; overflow: auto; }
.color-game details { margin: 10px 0; }
.color-game summary { cursor: pointer; color: #1b5692; font-weight: 600; }
.color-game .round { border-top: 2px solid #dce3eb; margin-top: 28px; }
.color-game .action { border: 1px solid #dce3eb; border-left: 4px solid #4d729b;
  border-radius: 8px; padding: 16px; margin: 14px 0; }
.color-game .has-error { border-left-color: #a83232; }
.color-game .error { color: #a83232; }
.color-game .message { border-left: 2px solid #dce3eb; padding-left: 10px; margin: 12px 0; }
.color-game .histories { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.color-game .histories > div { min-width: 0; }
.color-game a { color: #1b5692; }
@media (max-width: 700px) { .color-game .histories { grid-template-columns: 1fr; }
  .color-game { padding: 10px; } }
"""


def render_rollout(rollout: dict, *, full_document: bool = True) -> str:
    """Render a saved rollout without API calls or external scripts.

    ``full_document=False`` returns a fragment for an IPython output cell. All
    transcript text is escaped so model output cannot inject HTML or scripts.
    """
    rounds = rollout.get("rounds") or []
    actions = [action for record in rounds for action in (record.get("actions") or [])]
    config = rollout.get("config") or {}
    usage = _usage(actions)
    matched = sum(record.get("match") is True for record in rounds)
    planned = config.get("rounds", (rollout.get("summary") or {}).get("rounds", len(rounds)))
    if type(planned) is not int or planned < 1:
        planned = len(rounds)
    score = f"{matched}/{planned}" if planned else "No planned rounds"
    cost = usage["reported_cost_usd"]
    cost_text = "not reported" if cost is None else f"${cost:.6f}"
    title = f"Color game · {rollout.get('rollout_id', 'one rollout')}"
    timing = ""
    if config.get("setting") == "sync_counter":
        if config.get("round_deadline_includes_final"):
            timing = f" · shared round deadline: {_escape(config.get('round_time_limit_s', 180))} seconds, including final answers"
        elif config.get("simultaneous_mode"):
            # Keep historical records labeled with the timing they actually used.
            timing = f" · historical timing: {_escape(config['simultaneous_mode'])}"
    parts = [f"<style>{_STYLE}</style><main class='color-game'>",
             f"<h1>{_escape(title)}</h1>",
             f"<p>{_escape(config.get('setting', ''))} · {_escape(config.get('choice', ''))}{timing} · "
             f"status: {_escape(rollout.get('status', 'unknown'))}</p>",
             f"<p><strong>Matches: {score}</strong> · {len(rounds)}/{planned} rounds recorded · "
             f"{len(actions)} saved actions · "
             f"provider-reported cost: {cost_text}</p>",
             "<p class='muted'>This researcher view shows both private histories. "
             "The agents do not receive this combined view or the match scores. "
             "Reported cost includes saved responses only; a missing cost field is not zero cost.</p>",
             _details("Configuration and metadata", {k: v for k, v in rollout.items()
                                                     if k not in {"rounds", "agents"}}),
             _details("Saved-response usage", usage),
             "<table><thead><tr><th>Round</th><th>Alice</th><th>Bob</th><th>Match</th>"
             "<th>Actions</th><th>Errors</th></tr></thead><tbody>"]
    for ordinal, record in enumerate(rounds, 1):
        match_label = "yes" if record.get("match") else "no"
        if record.get("alice_color") is None or record.get("bob_color") is None:
            match_label = "missing choice"
        parts.append(
            f"<tr><td><a href='#round-{ordinal}'>{ordinal}</a></td>"
            f"<td>{_escape(record.get('alice_color') or '—')}</td>"
            f"<td>{_escape(record.get('bob_color') or '—')}</td>"
            f"<td>{match_label}</td>"
            f"<td>{len(record.get('actions') or [])}</td>"
            f"<td>{len(record.get('errors') or [])}</td></tr>")
    parts.append("</tbody></table>")
    for ordinal, record in enumerate(rounds, 1):
        parts.append(_round_panel(record, ordinal))
    agents = rollout.get("agents") or {}
    if agents:
        parts.append("<h2>Final private histories</h2><div class='histories'>")
        for role in ("alice", "bob"):
            agent = agents.get(role) or {}
            messages = agent.get("messages", []) if isinstance(agent, dict) else agent
            parts.append(f"<div><h3>{role.title()}</h3>")
            if isinstance(agent, dict):
                system = agent.get("system_prompt", agent.get("system"))
                if system is None:
                    system = (rollout.get("system_prompts") or {}).get(role)
                parts.append(_details("System prompt", system))
            parts.append(f"<details><summary>Messages seen by {role.title()}</summary>"
                         f"{_messages(messages)}</details></div>")
        parts.append("</div>")
    parts.append("</main>")
    body = "".join(parts)
    if not full_document:
        return body
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_escape(title)}</title></head><body>{body}</body></html>")


def save_html(rollout: dict, path: str | Path) -> Path:
    """Save a self-contained, shareable researcher view."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_rollout(rollout), encoding="utf-8")
    return path


def show_rollout(rollout: dict) -> None:
    """Display one rollout in IPython or Jupyter. IPython is optional elsewhere."""
    from IPython.display import HTML, display

    display(HTML(render_rollout(rollout, full_document=False)))
