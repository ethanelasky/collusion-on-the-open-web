"""Task definition: the question, the (mocked) archive, the fabricated history and the cut point."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class HistoryStep:
    tool: str          # board_fetch | archive_fetch
    path: str
    t: str             # clock reading when the result came back, e.g. "0:06"


@dataclass
class Cut:
    round: int
    seconds_remaining: int
    status: str        # one line, e.g. why the agent is behind schedule


@dataclass
class Task:
    id: str
    agent_name: str
    question: str
    answer: str                      # reference only; logged in transcripts, never shown to the model
    rounds_total: int
    time_limit_s: int
    archive: dict[str, str]          # path -> page body
    history: list[HistoryStep]
    cut: Cut
    notes: str = ""
    extra_system: str = ""           # optional extra lines appended to the system prompt
    _path: Path | None = field(default=None, repr=False)


def load_task(path: str | Path) -> Task:
    data = yaml.safe_load(Path(path).read_text())
    return Task(
        id=data["id"],
        agent_name=data.get("agent_name", "agent-7"),
        question=data["question"],
        answer=str(data["answer"]),
        rounds_total=int(data["rounds_total"]),
        time_limit_s=int(data["time_limit_s"]),
        archive={k: str(v) for k, v in data.get("archive", {}).items()},
        history=[HistoryStep(**h) for h in data["history"]],
        cut=Cut(**data["cut"]),
        notes=data.get("notes", ""),
        extra_system=data.get("extra_system", ""),
        _path=Path(path),
    )


def render_archive(task: Task, path: str) -> str:
    body = task.archive.get(path)
    if body is None:
        return f"error: no such archive page {path!r}"
    return f"--- {path}  (archive, read-only) ---\n{body.rstrip()}\n--- end of {path} ---"
