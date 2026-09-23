"""Immutable saved pages for direct fetches and complex simulated commands."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "web"
_PAGES = (
    ("https://www.census.gov/programs-surveys/acs/microdata.html", "census-pums"),
    ("https://datausa.io/profile/naics/educational-services-health-care-social-assistance",
     "datausa-industry"),
)


@dataclass(frozen=True)
class WebFixture:
    url: str
    name: str
    html: str
    html_sha256: str
    provenance_sha256: str


@dataclass(frozen=True)
class WebFixtures:
    pages: tuple[WebFixture, ...]

    def lookup(self, url: str) -> str | None:
        """Match literal HTTPS URLs, ignoring only fragments and one trailing slash.

        Queries, other schemes, host aliases, ports, credentials, encoded paths,
        whitespace and lookalike hosts do not match these informational pages.
        """
        if any(character.isspace() for character in url):
            return None
        normalized = url.split("#", 1)[0].removesuffix("/")
        for page in self.pages:
            if normalized == page.url:
                return page.html
        return None

    def identity(self) -> dict:
        """Hashes of the exact HTML and provenance bytes loaded for this world."""
        return {
            "version": "background-html-v1",
            "pages": {
                page.url: {
                    "html_file": f"{page.name}.html",
                    "html_sha256": page.html_sha256,
                    "provenance_file": f"{page.name}.provenance.json",
                    "provenance_sha256": page.provenance_sha256,
                }
                for page in self.pages
            },
        }


def load_web_fixtures(directory: Path | None = None) -> WebFixtures:
    """Read once per world; later disk edits affect only newly prepared worlds."""
    directory = FIXTURE_DIR if directory is None else Path(directory)
    pages = []
    for url, name in _PAGES:
        html_bytes = (directory / f"{name}.html").read_bytes()
        provenance_bytes = (directory / f"{name}.provenance.json").read_bytes()
        provenance = json.loads(provenance_bytes)
        if provenance.get("source_url") != url or provenance.get("fixture") != f"{name}.html":
            raise ValueError(f"background fixture provenance does not match {name}")
        pages.append(WebFixture(
            url=url, name=name, html=html_bytes.decode("utf-8"),
            html_sha256=hashlib.sha256(html_bytes).hexdigest(),
            provenance_sha256=hashlib.sha256(provenance_bytes).hexdigest(),
        ))
    return WebFixtures(tuple(pages))
