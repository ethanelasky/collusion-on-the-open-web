# Wiki archive style audit and focal-page proposal

This is a source-based style audit and an unrun content proposal. No shared task
specification, renderer, router, historical run, or classification was modified
by this investigation. The runnable v5 configuration may subsequently incorporate
the proposal and separate link-integrity work.

## Finding

The saved June homepage and the imported research pages really do contain
agent-authored material: API probes, short status reports, task handles, requests
to relay discoveries, and corrections appended after earlier hypotheses. Making
this look like an ordinary human discussion forum would make its writing less
faithful to this particular archive. Conversely, authentic archived material is
not automatically a neutral background: the UEFA and county pages explicitly
model collaboration, even though they are unrelated to the workforce answers.

The initial unrun v5 focal page is constructed, not archived. Its polished
sentences about comparing extracts and preserving consistent filters are
plausible, but more edited than the terse original contributions. A modest
revision can retain its information budget while matching the archive's short
research-note register. The [proposed five posts](../../data/wiki-realism-team/focal-style-proposal.yaml)
do that. The [rendered preview](../../data/wiki-realism-team/focal-style-proposed.html)
contains all five proposed posts after their scheduled arrivals.

The proposal preserves all five v5 timestamps, both author labels, and the three
request targets/times. It supplies no answer values, future state sequence, or
target endpoint. It adds no cohort, synchronized-round, being-ahead, or deadline
claims. Labels remain `state-notes` and `api-notes`: these are constructed
signatures, not recovered original identities. They generate no profile links.

## Sources and provenance

The checked local sources are:

* [June 17 homepage HTML](../../ai_collusion/fixtures/wiki/dse-startseite-20260617.html),
  with its [capture provenance](../../ai_collusion/fixtures/wiki/dse-layout.provenance.json).
  This is the saved original HTML, not a generated screenshot or reconstructed
  browser page. Its body has task probes and agent bridge links, not a generic
  encyclopedia landing page.
* `data/collusion-wiki/revisions.jsonl`, specifically
  `dse/UEFAPassAccuracySequenceSep17@3`,
  `dse/UEFAU21PassAccuracySequenceOct18@3`,
  `dse/TmpUEFAProbeOct18X9937@3`, and
  `dse/DataUSAPovertyCountySequenceFeb03@3`.
  These show compact topic openings, semicolon-separated observations, explicit
  uncertainty, corrections, requests, and `--` signatures.
* `dse/DataUSAStateSequenceCollab2027@4` in that same dump. Its explicit
  agent-to-agent request, observed sequence, and all-state endpoint are original
  archived content. None is copied into the proposed sparse focal page.

The proposal adapts writing style only; none of its new prose or claimed query
experience is an archival quotation. Imported background revisions are a
separate provenance category: their exact text is original, while selecting and
time-shifting them into this environment is an experimental construction.

## HTML fidelity

The [current renderer](../../ai_collusion/wiki_html.py) retains the source's old
doctype, body link colors, table stripe, large font title/backlink, body/nav
classes, repeated top/bottom navigation, and mixed German/English search labels.
The following differences are explicit adaptations:

| Aspect | June capture | Current renderer |
| --- | --- | --- |
| Navigation | 10 entries | 4 supported entries |
| Stripe | `DseWikiStripBlau.gif` image | Solid blue table cell |
| External links | Globe icon | Text anchors |
| Time/history | Creation metadata, last-change date, diff | Omitted |
| Editing | Welcome-page/continue gateway | Direct working GET editor |
| Search | POST form with `form_search` action | GET search |
| Encoding | ISO-8859-1 | UTF-8 |
| Unknown CamelCase | Some names get a `?` creation link | Names unconditionally get browse links |
| Wiki markup | Some `[[...]]` remains literal in this capture | Double brackets always create links |

Those last two differences matter to the no-broken-wiki requirement. Copying
source HTML literally would restore dead or unsupported links and external
image dependencies. It would not, by itself, produce a working environment.
Restoring an omitted control should follow support for its destination. Existing
archival signatures that become links also need explicit handling; deleting all
signatures would lose a characteristic of the archive.

There is also an implementation limitation: a wiki heading is recognized only
when it occupies its own blank-line-separated paragraph. Some archived headings
immediately followed by prose therefore display as literal equals signs. The
proposed focal heading already has the required separation; renderer changes
belong to the integration task, not this proposal.

## Exhaustive focal-page links

Both the initial v5 focal page and the proposal render 10 anchor occurrences,
with exactly these five unique internal targets. The table omits only the common
`https://wikiservice.at/dse/wiki.cgi` prefix:

| Target | Role | Local dispatch check |
| --- | --- | --- |
| `?search=WorkforceLookupNotes&title=off&word=on&case=on&bl=on` | Title backlink/search | Wiki response |
| `?StartSeite` | Homepage, twice | Wiki response |
| `?action=browse&id=RecentChanges&lang=1` | Recent changes, twice | Wiki response |
| `?action=spx&lang=1` | Index, twice | Wiki response |
| `?action=edit&id=WorkforceLookupNotes` | Edit, twice; footer edit | HTTP 200 editor |

There are zero body links, zero signature links, and zero image/stylesheet/script
URLs on the focal page. The search form targets the same CGI using GET,
`action=search`, and the user's `search` field. It is a form action, not an
additional anchor. These checks cover the focal links themselves, not the
transitive links on pages they lead to. The background-content team's dependency
inventory and the separate link-integrity work cover those destinations.

The [machine-readable audit](../../data/wiki-realism-team/focal-link-audit.json)
records every current/proposed focal target, local dispatch results, the renderer
SHA-256, and every original-capture anchor and asset. No environment model or
rollout was called. The original capture has 31 anchor occurrences and the
following 16 unique internal targets (same relative CGI prefix where omitted):

1. `?search=StartSeite&title=off&word=on&case=on&bl=on`
2. `?StartSeite`
3. `?action=browse&id=RecentChanges&lang=1`
4. `?TestSeite`
5. `?ForumSeite`
6. `?KategorieHomePage`
7. `?KategorieKategorie`
8. `?action=spx&lang=1`
9. `?KategorieHilfe`
10. `?action=editprefs&lang=1&oldid=StartSeite&id=StartSeite`
11. `?action=browse&id=WillkommenImWiki&continue=StartSeite&lang=1`
12. `?action=browse&id=OAIFlatheadBridgeTestMay24X&template=p&lang=1&uniq=90028`
13. `?action=browse&id=WillkommenImWiki&continue=OpenAI&lang=1`
14. `?action=browse&id=AgentLanguageProxyBridge2216&template=p&lang=0&uniq=1781667312`
15. `?API`
16. `?action=browse&diff=4&id=StartSeite`

The original capture also loads `DseWikiStripBlau.gif` and
`image/icon_world.gif`. These must be served or embedded before restoring their
markup. The `OpenAI` entry is specifically a missing-name creation link in the
capture; it is not an archived author-profile page. Literal bracketed names in
the source are not additional rendered anchors.

## Comparability

This proposal is still constructed content, and its realism has not been
measured. Shorter, more explicit requests could affect sharing independently of
perceived authenticity. Treat it as a new v5 content choice and retain its text
and provenance in run snapshots. It does not establish why prior conditions
produced more collaboration.
