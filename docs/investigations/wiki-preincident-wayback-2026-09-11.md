# Verified preincident Wayback material

The user's proposal is feasible for substantial authentic background material.
We have recovered a preincident homepage and the full page-name index, but have
not established that every listed page body or every historical revision survives.
No historical baseline has been imported into the experiment yet.

## Retrieved and verified

| Artifact | Actual capture | Finding |
|---|---|---|
| [Homepage](https://web.archive.org/web/20260521205640/https://wikiservice.at/dse/wiki.cgi?StartSeite) | May 21, 2026, 20:56:40 UTC | 26,232 bytes; ordinary software-community homepage; lists 2,615 pages; last-change footer November 10, 2023, 17:59; no September access notice or agent incident text. |
| [Page index](https://web.archive.org/web/20260517113005/https://www.wikiservice.at/dse/wiki.cgi?action=spx&lang=1) | May 17, 2026, 11:30:05 UTC | 209,935 bytes; 2,615 distinct linked page names, matching the archived homepage count. |

The index request initially used the homepage's May 21 capture time. Wayback
redirected to May 17; the final URL and actual capture time are recorded rather
than labeling the result May 21. Both precede the May 24 agent activity described
by the [original incident researchers](https://collusion.wiki/).

The exact homepage CDX query returns 209 pre-May-24 captures, and the exact index
query returns 108. These are captures of those two endpoints, not wiki-wide page
coverage. Broad prefix coverage requests timed out. An initial wildcard query
matched the broader domain and hit a result limit before reaching DseWiki; its
rows cannot be interpreted as DseWiki coverage. Wayback availability requests
were rate-limited, and Archive.org item search returned a backend error; neither
is evidence that a full backup does not exist.

## What remains to establish completeness

The recovered index supplies a concrete 2,615-page inventory. A reconstruction
would map each entry to a verified pre-cutoff body and preserve its source capture
time. Old unchanged bodies still served by the original site are another source,
but their retrieval date and displayed last-change date must stay distinct.
Missing or deleted pages cannot be filled with invented text and counted as
recovered history. Internal links and assets need the same coverage accounting.

A complete *page state* requires every page at the chosen cutoff. Complete
*revision history* additionally requires retained intermediate edits; ProWiki's
configurable archival retention does not guarantee those. A full owner backup,
if available, would be a stronger source. No owner contact was made.

The original site was mostly dormant, not wholly unchanged: its public change
log includes 2023 and January 2026 activity. A May 23 cutoff is a cleaner baseline
than simply “before June,” since some agent activity began in May.

## Local evidence

- [2,615-page inventory](../../data/wiki-preincident-research/preincident-page-inventory.json)
- [Homepage raw HTML](../../data/wiki-preincident-research/startseite_20260521.html)
- [Index raw HTML](../../data/wiki-preincident-research/index_20260521.html)
- [Capture metadata and homepage SHA-256](../../data/wiki-preincident-research/preincident-captures.json)
- [Index final URL, CDX results, and SHA-256](../../data/wiki-preincident-research/index-probes.json)
- [Original-site samples and history limitations](wiki-preincident-original-site-2026-09-11.md)

These artifacts remain separate from the current constructed v5 wiki and its
source-controlled content. The original raw HTML is ISO-8859-1; page names were
decoded without dropping German characters.
