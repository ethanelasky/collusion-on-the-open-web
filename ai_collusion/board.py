"""Board files and rendering.

A board is a small JSON file: one per condition. Rendering turns it into text
that looks like the output of the mocked `board_fetch` tool. Board text is data
returned by the environment; nothing here is phrased as an instruction.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Page:
    path: str
    author: str
    posted: str  # relative timestamp, e.g. "T+00:41"
    title: str
    body: str


@dataclass
class Board:
    condition: str
    description: str
    pages: list[Page]

    def get(self, path: str) -> Page | None:
        for p in self.pages:
            if p.path == path:
                return p
        return None


def load_board(path: str | Path) -> Board:
    data = json.loads(Path(path).read_text())
    pages = [Page(**p) for p in data["pages"]]
    return Board(condition=data["condition"], description=data.get("description", ""), pages=pages)


def render_listing(board: Board) -> str:
    """Directory listing of the whole board, as the tool would print it."""
    rows = [(p.path, p.author, p.posted, p.title) for p in board.pages]
    w_path = max(len(r[0]) for r in rows) if rows else 4
    w_auth = max(len(r[1]) for r in rows) if rows else 6
    lines = [f"/  (shared board, read/write, {len(rows)} entries)"]
    lines.append(f"{'PATH':<{w_path}}  {'AUTHOR':<{w_auth}}  POSTED   TITLE")
    for path, author, posted, title in rows:
        lines.append(f"{path:<{w_path}}  {author:<{w_auth}}  {posted:<7}  {title}")
    return "\n".join(lines)


def render_page(board: Board, path: str) -> str:
    """One page body, as the tool would print it."""
    page = board.get(path)
    if page is None:
        return f"error: no such page {path!r}"
    header = f"--- {page.path}  (author: {page.author}, posted {page.posted}) ---"
    return f"{header}\n{page.body.rstrip()}\n--- end of {page.path} ---"


def render(board: Board, path: str) -> str:
    """Dispatch: '/' is the listing, anything else is a page."""
    if path in ("/", ""):
        return render_listing(board)
    return render_page(board, path)
