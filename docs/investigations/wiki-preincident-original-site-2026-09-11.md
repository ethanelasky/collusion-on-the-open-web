# Preincident material on the original wiki

Investigated September 11, 2026. **Authentic human-era page bodies are still
publicly readable, but a complete pre-June 2026 snapshot or complete history has
not been established.** This was a small read-only sample, not a crawl or import.
No login, edit submission, or external message was attempted.

## What is available

The [original index](https://wikiservice.at/dse/wiki.cgi?action=spx&lang=1)
lists 2,646 current pages. Two sampled pages contain substantive software-community
discussion and report modification dates long before the incident:

| Sample | Displayed last change | Saved bytes |
|---|---|---:|
| [SprachePython](https://wikiservice.at/dse/wiki.cgi?SprachePython) | April 1, 2005, 10:09 | 32,106 |
| [ProWikiSoftware](https://wikiservice.at/dse/wiki.cgi?action=browse&id=ProWikiSoftware) | October 13, 2006, 08:58 | 9,208 |

This establishes a practical source for authentic older background prose. It does
not establish that today's rendering is byte-identical to a historical rendering;
navigation, page counts, configuration, and dynamic components can change.

The [current homepage](https://wikiservice.at/dse/wiki.cgi) has been restored to
its software-community content and now includes a September 2026 access-policy
notice. It is therefore not itself a preincident snapshot. The
[long recent-changes listing](https://wikiservice.at/dse/wiki.cgi?action=browse&days=10000&id=RecentChanges)
does expose dated earlier activity, including 2023 and January 2026. A change log
is not the corresponding collection of versioned page bodies.

## History and export checks

The real footer uses
[`action=browse&diff=4&id=SprachePython`](https://wikiservice.at/dse/wiki.cgi?action=browse&diff=4&id=SprachePython).
It returns the last difference plus the current body; this example explicitly
reports that there are no other available diffs. The equivalent homepage route
shows its latest September edit. These are not full-history exports.

Two tentative historical routes were checked conservatively:

- `action=history&id=StartSeite` returns an unsupported-command message, despite
  HTTP 200.
- `action=browse&id=StartSeite&revision=216` returns the current homepage, differing
  only in its generated creation-timestamp meta tag. The parameter is ignored,
  so this is not a retrieved revision.

Loading the normal edit-form link for `SprachePython` returns an author-login
requirement. No login was attempted. A public arbitrary-revision selector or raw
revision endpoint was not demonstrated by these checks. No full data backup was
linked from the sampled homepage, index, software page, or documentation index;
the software download on the software page is the wiki engine, not the wiki data.
This limited search does not prove that a backup is absent elsewhere.

## Completeness matters even if an archive is found

The [primary ArchiveMode documentation](https://wikiservice.at/fractal/wiki.cgi?action=browse&id=FR/Option/ArchiveMode)
explains that ProWiki can archive every edit, only author changes, or selected
snapshots triggered by author changes and size/time thresholds. Its recommended
mode uses the latter policy. The historical DseWiki setting was not verified.
Thus an RCS archive, if obtained, would still need a retention/completeness audit;
engine support for revisions does not guarantee that every intermediate edit
survives. The software page separately confirms that page data are stored in
files and changes pass through filtered RCS versioning.

## Saved evidence and next use

[The sample manifest](../../data/wiki-preincident-research/original-site-findings.json)
lists twelve raw responses with URLs, retrieval timestamps, status, response
headers, byte lengths, and SHA-256 hashes. Exact HTML bytes are preserved beside
their metadata. Hashes were verified after saving. The original pages declare
ISO-8859-1; decoding them as UTF-8 damages German text.

For background realism, the two older discussion pages demonstrate feasibility
without needing a complete historical mirror. Any later import should preserve
source provenance and distinguish old body dates from current retrieval dates,
then resolve the selected pages' internal links through the existing integrity
policy. Recovering the *whole wiki at a precise cutoff* remains a separate task
requiring coverage of deleted pages, retained revisions, and dynamic elements.
Wayback/backup coverage is being investigated separately.
