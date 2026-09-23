# Public-web host evidence atlas

Open [the standalone artifact](../host-atlas.html) in a browser. It works offline
and contains its own styles, scripts, data, and source excerpts. External links
lead to the sanitized collusion.wiki explorer; historical service URLs inside
quotations are displayed as text.

The artifact has 21 service profiles, six case studies, and 47 exact excerpts.
It includes host/function filters, three citation metrics, a daily activity
chart and table, searchable evidence, expandable changed-hunk context, citation
copying, JSON download, and light/dark themes.

The **Coded communication** panel collects six examples by how a sender encodes
information and a receiver reads it: state counters, a numeric answer code,
counter protocol repairs, public request logs, shortener reply tags, and a
proposed process heartbeat. Each includes exact excerpts and distinguishes
reported observations from unverified proposals. Open `#communication` to jump
directly to the panel.

## Build

From the repository root:

```sh
python3 scripts/build_host_atlas.py
```

Inputs:

- `data/collusion-wiki/revisions.jsonl`: the supplied sanitized archive.
- `scripts/build_host_atlas.py`: editorial profiles, case studies, quote
  selectors, count definitions, and extraction/validation logic.
- `docs/host-atlas/atlas.template.html`: presentation and interaction code.
- `docs/host-atlas/communication.json`: summaries and evidence references for
  the coded and unconventional communication panel; included in downloaded data.

Outputs:

- `docs/host-atlas.html`: standalone artifact.
- `docs/host-atlas/data.json`: readable copy of the embedded data.

The builder performs no network calls and uses only Python's standard library.
It validates that every excerpt is inside an actual inserted/replaced hunk and
stores its exact character offset in the full JSON-decoded revision body.
`text_sha256` hashes the UTF-8 encoding of that decoded body;
`recorded_body_sha256` preserves the source record's own hash. They can differ
in this supplied archive, so neither is substituted for the other. The whole
input JSONL file is also hashed and identified in the artifact.

## Count definitions

All host matching uses explicit, case-insensitive text regexes listed in the
downloadable data and each expanded profile. These are citation searches,
including encoded/nested URLs and plain text; they do not parse executed
network requests. Each revision counts at most once within a host group.

- **Changed-text revisions:** matching text in an inserted or replaced hunk.
  Replacements may retain old links and are not necessarily new introductions.
- **Full-body revisions:** matching text anywhere in the saved body, including
  material carried forward from earlier revisions.
- **Distinct pages:** a page with a match in any saved body. Daily page counts
  place it on the date of its first matching saved revision.
- **Author labels:** nonempty `label` values among changed-text matches. An
  author label is not treated as an independent person or model instance.

The corpus is 14,591 revisions, 4,579 pages, and 3,102 nonempty author labels;
899 revisions have an empty label. The snapshot covers May 24–July 2, 2026.
Counts are recomputed, not copied from the earlier artifact.

## Research provenance

The previous artifact was `host-usage.html` in the scratchpad of Claude session
`7122ff29-3f39-4e15-8aa9-90d12fdaf72c` (tmux `17:1.1`). Leads also came from that
session's consolidated discovery report, timeline report, URL/link audit,
covert-channel audit, and their saved excerpts. They were treated as research
leads rather than authoritative findings. Every displayed quotation was
re-extracted from the repository's revision file. No private agent reasoning or
session transcript is embedded in this artifact.

The atlas adds the CounterAPI test contamination and key repair, the failed
YOURLS tag lookup and correction, separate tasks illustrating accelerated
task-time coordination, nested retrieval routes, termination protocols, and
published claims of workaround reuse. It distinguishes proposals, self-reports,
and directly observed saved posts. It does not reuse the reports' unsupported
claims that search discovery was ruled out, all agents were independent, or
absence of a found covert channel proves none existed.

## Verification

The artifact was exercised in Chromium at desktop and 390px mobile widths,
in light and dark themes. Host and evidence search, function filtering, all
three metrics, all six case studies, chart selection, source dialogs, table
views, and JSON downloads passed. No page errors, horizontal viewport overflow,
or background network requests were observed. Desktop, mobile, host-profile,
and case-study screenshots were visually inspected.
