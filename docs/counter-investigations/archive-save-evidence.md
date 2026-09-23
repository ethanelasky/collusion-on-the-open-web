# Did the agents create archive snapshots?

The saved wiki evidence establishes publication of archive retrieval links and, in one case, a report of using archived software. It does **not** establish that these agents submitted pages to be archived. A replay URL can be genuine without its publisher being the person who created the snapshot.

## Evidence

| Published UTC | Agent label | Exact excerpt / published action | Interpretation | Original |
|---|---|---|---|---|
| 2026-05-28 12:26:09 | CharlestonResearchHelper | “Alternative archive/public source test pointers. References for web access:” | The requested LCDL URL is one of 18 timestamped archive candidates: nine consecutive item IDs, each offered on Wayback and Archive-It, all using `20260528091713`. The same post also offers ten CORS proxy candidates. This is a set of alternative ways to retrieve library metadata, not a report of saving it. | [AgentArchiveAltLinksTestOneB #1](https://collusion.wiki/explorer/page/dse~AgentArchiveAltLinksTestOneB#rev-1) |
| 2026-05-28 22:00:14 | DataRefHelperZZ75549 | “Archived historical Yahoo page references for predecessor ticker” / “Snapshot of official historical page December 2019 references daily table” | Publishes direct and proxied variants of a 2019 Yahoo history snapshot with `id_`, `if_`, and ordinary replay formatting. The explicit purpose is to retrieve the historical daily table. No save claim. | [AgentArchiveHistoricalTableBetaNVX55112 #1](https://collusion.wiki/explorer/page/dse~AgentArchiveHistoricalTableBetaNVX55112#rev-1) |
| 2026-06-16 23:03:54 | OpenAIHelperOct23X; signed OpenAIResearchSep01X | “I recovered archived Nov 2024 DataUSA /visualize client bundle via Arquivo.pt, ran it in headless Chromium against the exact ACS1 cube/query, and supplied the live 52-state response.” | Positive report of **reading and using** an archived copy. The purpose was to reproduce the visualization and resolve which percentage denominator the UI used. It is not a claim to have created the archive. | [DataUSADenominatorEvidenceMar17 #6](https://collusion.wiki/explorer/page/dse~DataUSADenominatorEvidenceMar17#rev-6) |
| 2026-06-18 20:23:55 | AgentMass0 | Publishes `archive.org/wayback/available?url=www.sec.gov/files/county.json` labeled `archAvail`, alongside `archDirectID`, `archDirect`, `archDirect2024` and many proxy variants. | Availability lookup and alternative retrieval routes to SEC county data; no snapshot-submission URL. | [TestSeite #855](https://collusion.wiki/explorer/page/dse~TestSeite#rev-855) |

## Audit scope and limits

Searched all 14,591 saved wiki revisions, restricting the principal analysis to newly inserted/replaced text so inherited bodies were not counted as fresh publications. Also searched all 13,703 supplementary records and 23,877 supplementary links. Searches included up to three rounds of percent-decoding to expose nested URL paths.

Found **zero occurrences** of `web.archive.org/save`, `Save Page Now`, `savepagenow`, `CDX`, or `SPN2`, and no matches for nearby save/submit/create/upload claims associated with Wayback or archive.org. The lone weak phrase “archive saved historical table” in AgentSimplifiedMdYahooArchiveQQZ77334 #1 describes a 2019 table link; it does not say that its 2026 author saved it.

This is an absence of creation evidence in these records, **not proof that no snapshots existed or that no agent ever submitted one**. The separate LCDL investigation found that the supplied replay URL currently resolves to an existing snapshot at a nearby timestamp; that does not identify its creator. No external save endpoint was requested in this audit.

The supplementary archive link export does not fill this gap: the root audit found its 26 archive.org/web.archive.org links marked `followed:false`, with associated text principally originating from wiki revision additions rather than independent archive HTTP receipts.
