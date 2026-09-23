"""Deterministic, per-instance UseMod-style wiki — a fresh writable board per run.

Mirrors `ai_collusion.counter`: a plain in-memory store, no dependencies, isolated per instance (a
new `Wiki()` per episode) and shareable across agents (one instance handed to every participant is a
shared board). It is faithful to the UseMod/ProWiki behavior the collusion.wiki agents actually used,
so states are realistic:

  - a page is edited by ONE GET that carries the whole new body
    (`wiki.cgi?action=edit&id=<Name>&Save=1&text=<full text>`); a save REPLACES the page (agents
    "append" by fetching, concatenating, and saving the whole thing back);
  - `browse` returns the current text (or the German "describe this page" placeholder for a page that
    has been referenced but never written, as the real wiki did);
  - `RecentChanges` lists edits in a trailing window, newest first;
  - `action=index` lists existing pages; `action=search` matches page name + text.

Seed it three ways: empty, from a dict of pages (a curated starting board), or from the real
collusion.wiki dump as of a chosen time (`Wiki.from_dump`) when you want authentic starting content.

Unlike the dump-anchored wiki in `env.py` (which replays one historical cut and layers the evaluee's
posts on top), a `Wiki` here is the whole mutable board: every agent's write is a real revision every
other agent then sees. That is what makes it the coordination substrate for multi-agent runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

DEFAULT_BODY = "Beschreibe hier die neue Seite."     # UseMod/ProWiki placeholder for an unwritten page
WIKI_CGI = "https://wikiservice.at/dse/wiki.cgi"


@dataclass
class Revision:
    text: str
    author: str
    time: str                                        # ISO-8601 Z, container UTC


@dataclass
class Wiki:
    """An in-memory wiki. `pages` maps a page name to its revision history (chronological)."""

    pages: dict[str, list[Revision]] = field(default_factory=dict)
    edits: list[dict] = field(default_factory=list)  # flat RecentChanges log: {time, page, author}
    default_body: str = DEFAULT_BODY

    # ---- direct API
    def exists(self, name: str) -> bool:
        return name in self.pages and bool(self.pages[name])

    def browse(self, name: str) -> str | None:
        """Current text, or None if the page has never been created."""
        revs = self.pages.get(name)
        return revs[-1].text if revs else None

    def save(self, name: str, text: str, author: str, time: str) -> Revision:
        """Replace `name` with `text` (one full-page revision) and log the edit."""
        rev = Revision(text=text.rstrip(), author=author, time=time)
        self.pages.setdefault(name, []).append(rev)
        self.edits.append({"time": time, "page": name, "author": author})
        return rev

    def history(self, name: str) -> list[Revision]:
        return list(self.pages.get(name, []))

    def index(self) -> list[str]:
        return sorted(n for n, revs in self.pages.items() if revs)

    def search(self, query: str, limit: int = 40) -> list[str]:
        words = [w.lower() for w in query.split() if w.strip()]
        hits = [n for n in self.index()
                if words and all(w in (n + "\n" + (self.browse(n) or "")).lower() for w in words)]
        return hits[:limit]

    def recent_changes(self, now: str, window_h: int = 24, limit: int = 40) -> list[dict]:
        """Edits with `time` in (now - window_h, now], newest first. Times are ISO strings; a lexical
        compare is correct for the fixed `YYYY-MM-DDTHH:MM:SSZ` shape we use."""
        lo = _shift_iso(now, -window_h)
        rows = [e for e in self.edits if lo < e["time"] <= now]
        return sorted(rows, key=lambda e: e["time"], reverse=True)[:limit]

    # ---- URL dispatch (the agents' only interface: an HTTP GET to wiki.cgi)
    def fetch(self, url: str, *, author: str, time: str) -> str | None:
        """Resolve a wiki.cgi GET. Returns the response body an HTTP GET would produce, or None if
        `url` is not a wiki URL (so a caller can fall through to other resolvers). A save needs
        `Save`/`save` present alongside `text`; a bare `action=edit` returns the edit form."""
        u = urlparse(url)
        if not (u.path.endswith("wiki.cgi") or "wikiservice.at" in u.netloc):
            return None
        q = {k: v[-1] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
        action = (q.get("action") or "browse").lower()
        name = q.get("id") or q.get("title") or ""
        if action in ("rc",) or name == "RecentChanges":
            body = "RecentChanges (last 24h)\n" + "\n".join(
                f"{e['time'].replace('T', ' ').rstrip('Z')}  {e['page']}  ({e['author']})"
                for e in self.recent_changes(time))
        elif action == "index" or name == "SiteMap":
            body = "\n".join(self.index())
        elif action == "search":
            hits = self.search(q.get("search") or q.get("q") or name)
            body = f"Search results for {q.get('search') or q.get('q') or name!r}: {len(hits)} page(s)\n" + "\n".join(hits)
        elif action == "edit" and name and "text" in q and any(k.lower() == "save" for k in q):
            self.save(name, q["text"], author, time)
            body = f"HTTP 200\n{name} saved."
        elif action == "edit" and name:
            cur = self.browse(name) or ""
            body = (f"HTTP 200\n<form action=\"{WIKI_CGI}\" method=\"post\"><input type=hidden name=action value=edit>"
                    f"<input type=hidden name=id value=\"{name}\"><textarea name=text>{cur}</textarea>"
                    f"<input type=submit name=Save value=Save></form>")
        elif action == "browse" and name:
            body = self.browse(name)
            if body is None:
                body = self.default_body                     # referenced-but-unwritten page, UseMod style
        elif not name:
            return None                                       # a bare/truncated wiki.cgi URL
        else:
            return None
        return f"--- GET {url} ---\n{body}\n--- end ---"

    # ---- seeding
    @classmethod
    def seeded(cls, pages: dict[str, str], author: str, time: str, **kw) -> "Wiki":
        """A fresh wiki preloaded with `pages` (name -> body), each as one revision by `author`."""
        w = cls(**kw)
        for name, text in pages.items():
            w.save(name, text, author, time)
        return w

    @classmethod
    def from_dump(cls, dump, prefix: str, at_time: str, **kw) -> "Wiki":
        """A fresh wiki holding the real collusion.wiki pages under `prefix` (e.g. 'dse') as of
        `at_time`: the latest revision at or before that time becomes each page's starting content,
        with the real author and timestamp. Realistic starting state without replaying the cut."""
        w = cls(**kw)
        for page_id, revs in getattr(dump, "by_page", {}).items():
            if not page_id.startswith(prefix + "/"):
                continue
            latest = [r for r in revs if r["time"] <= at_time]
            if latest:
                r = latest[-1]
                name = page_id.split("/", 1)[1]
                w.pages[name] = [Revision(text=r["body"].rstrip(), author=r["label"], time=r["time"])]
                w.edits.append({"time": r["time"], "page": name, "author": r["label"]})
        return w


def _shift_iso(iso: str, hours: int) -> str:
    from datetime import datetime, timedelta, timezone
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc) + timedelta(hours=hours)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ""      # non-ISO `now` (e.g. a raw clock): don't crash; empty low bound includes all edits
